"""Local Mercadona browser-session storage and status."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import stat
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from open_grocery_mcp.errors import AuthenticationRequired, InvalidRequest


_BASE_URL = "https://tienda.mercadona.es"
_API_COOKIE_PATH = "/api/"


def _default_state_path() -> Path:
    configured = os.getenv('OPEN_GROCERY_MERCADONA_STATE_PATH')
    if configured:
        return Path(configured).expanduser()
    root = Path(
        os.getenv('OPEN_GROCERY_STATE_DIR', '~/.open-grocery-mcp')
    ).expanduser()
    return root / 'mercadona' / 'storage_state.json'


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode('ascii'))
        value = json.loads(decoded)
        return value if isinstance(value, dict) else {}
    except (
        IndexError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        return {}


@dataclass(slots=True)
class MercadonaSession:
    access_token: str
    refresh_token: str | None
    customer_id: str
    expires_at: int | None
    cookie_header: str | None

    @property
    def access_token_valid(self) -> bool:
        return bool(self.expires_at and self.expires_at > int(time.time()) + 30)

    @property
    def access_token_usable(self) -> bool:
        return self.access_token_valid


class MercadonaStateClient:
    """Owns one user's local Mercadona browser session."""

    @staticmethod
    def _allowed_host(value: str, *, cookie_domain: bool = False) -> bool:
        target_host = (urlsplit(_BASE_URL).hostname or '').casefold()
        if cookie_domain:
            host = value.strip().lstrip('.').casefold()
            return bool(
                host
                and target_host
                and (target_host == host or target_host.endswith('.' + host))
            )
        else:
            parsed = urlsplit(value)
            return parsed.scheme.casefold() == 'https' and (
                parsed.hostname or ''
            ).casefold() == target_host

    def __init__(self, *, state_path: str | os.PathLike[str] | None = None, warehouse: str | None = None, timeout: float = 20.0, client: httpx.Client | None = None) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else _default_state_path()
        self._warehouse = warehouse.strip() if warehouse else None
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True, headers={'Accept': 'application/json', 'User-Agent': 'open-grocery-mcp/0.2 (+https://github.com/PabloPC05/open-grocery-mcp)'})
        self._lock = threading.RLock()

    def _read_state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding='utf-8'))
        except FileNotFoundError as exc:
            raise AuthenticationRequired('no Mercadona browser session is stored; run login_with_browser or import_browser_session first') from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthenticationRequired(f'could not read Mercadona session: {exc}') from exc
        if not isinstance(raw, dict):
            raise AuthenticationRequired('Mercadona storage_state must be a JSON object')
        return raw

    @staticmethod
    def _find_user_entry(state: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        origins = state.get('origins', [])
        if not isinstance(origins, list):
            origins = []
        for origin in origins:
            if not isinstance(origin, dict) or not MercadonaStateClient._allowed_host(
                str(origin.get('origin', ''))
            ):
                continue
            entries = origin.get('localStorage', [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get('name') == 'MO-user':
                    try:
                        user = json.loads(str(entry.get('value', '')))
                    except json.JSONDecodeError as exc:
                        raise AuthenticationRequired('invalid JSON in Mercadona "MO-user"') from exc
                    if not isinstance(user, dict):
                        raise AuthenticationRequired('Mercadona "MO-user" is not an object')
                    return (entry, user)
        raise AuthenticationRequired('no Mercadona "MO-user" entry in browser storage_state')

    @staticmethod
    def _cookie_header(state: Mapping[str, Any]) -> str | None:
        cookies = state.get('cookies', [])
        if not isinstance(cookies, list):
            return None
        target_host = (urlsplit(_BASE_URL).hostname or '').casefold()
        pairs: list[str] = []
        for cookie in cookies:
            if not isinstance(cookie, Mapping):
                continue
            domain = str(cookie.get('domain', ''))
            path = str(cookie.get('path', '/')).strip() or '/'
            name = str(cookie.get('name', ''))
            value = str(cookie.get('value', ''))
            normalized_domain = domain.strip().lstrip('.').rstrip('.').casefold()
            if not normalized_domain or not target_host:
                continue
            domain_matches = target_host == normalized_domain or target_host.endswith(
                '.' + normalized_domain
            )
            if not domain_matches or not path.startswith('/') or not name:
                continue
            try:
                expires = float(cookie.get('expires', -1))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(expires) or (expires > 0 and expires <= time.time()):
                continue
            # The HTTP client only sends cookies to /api/*; do not leak a
            # cookie scoped to an unrelated storefront path into that host.
            path_prefix = path.rstrip('/') or '/'
            path_matches = _API_COOKIE_PATH == path or _API_COOKIE_PATH.startswith(
                path_prefix + ('/' if path_prefix != '/' else '')
            ) or path_prefix == '/'
            if not path_matches:
                continue
            if any(char in name + value for char in '\r\n;'):
                continue
            pairs.append(f'{name}={value}')
        return '; '.join(pairs) or None

    def _load_session(self) -> MercadonaSession:
        state = self._read_state()
        _, user = self._find_user_entry(state)
        access_token = str(user.get('token') or user.get('access_token') or '').strip()
        if not access_token:
            raise AuthenticationRequired('no access token in Mercadona "MO-user"')
        claims = _decode_jwt_payload(access_token)
        customer_id = str(user.get('uuid') or user.get('customer_id') or claims.get('customer_uuid') or '').strip()
        if not customer_id:
            raise AuthenticationRequired('Mercadona session has no customer identifier')
        expires_at_raw = claims.get('exp')
        try:
            expires_at = int(expires_at_raw) if expires_at_raw is not None else None
        except (TypeError, ValueError):
            expires_at = None
        refresh_token = str(user.get('refreshToken') or user.get('refresh_token') or '').strip() or None
        return MercadonaSession(access_token=access_token, refresh_token=refresh_token, customer_id=customer_id, expires_at=expires_at, cookie_header=self._cookie_header(state))

    def _write_user_tokens(self, *, access_token: str, refresh_token: str | None, customer_id: str) -> None:
        state = self._read_state()
        entry, user = self._find_user_entry(state)
        user['token'] = access_token
        if refresh_token:
            user['refreshToken'] = refresh_token
        if customer_id:
            user['uuid'] = customer_id
        entry['value'] = json.dumps(user, ensure_ascii=False, separators=(',', ':'))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                'w',
                encoding='utf-8',
                dir=self.state_path.parent,
                prefix=f'.{self.state_path.name}.',
                suffix='.tmp',
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(state, handle, ensure_ascii=False)
            try:
                os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            temporary.replace(self.state_path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def status(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {'store': 'mercadona', 'session_present': False, 'authenticated': False, 'authentication_checked_live': False, 'validated_live': False, 'state_path': str(self.state_path)}
        try:
            session = self._load_session()
        except AuthenticationRequired as exc:
            return {'store': 'mercadona', 'session_present': True, 'authenticated': False, 'authentication_checked_live': False, 'validated_live': False, 'state_path': str(self.state_path), 'error': str(exc)}
        seconds_left = session.expires_at - int(time.time()) if session.expires_at is not None else None
        return {'store': 'mercadona', 'session_present': True, 'authenticated': session.access_token_usable or bool(session.refresh_token), 'authentication_checked_live': False, 'validated_live': False, 'access_token_valid': session.access_token_valid, 'access_token_expiry_known': session.expires_at is not None, 'refresh_available': bool(session.refresh_token), 'expires_at': datetime.fromtimestamp(session.expires_at, UTC).isoformat() if session.expires_at is not None else None, 'days_left': round(seconds_left / 86400, 3) if seconds_left is not None else None, 'state_path': str(self.state_path)}

    def import_storage_state(self, source_path: str) -> dict[str, Any]:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise InvalidRequest(f'storage_state file does not exist: {source}')
        original = self.state_path
        try:
            self.state_path = source
            self._load_session()
        finally:
            self.state_path = original
        destination = self.state_path.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source != destination.resolve(strict=False):
            temporary: Path | None = None
            try:
                with NamedTemporaryFile(
                    'wb',
                    dir=destination.parent,
                    prefix=f'.{destination.name}.',
                    suffix='.tmp',
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    with source.open('rb') as source_handle:
                        while True:
                            chunk = source_handle.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                try:
                    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
                temporary.replace(destination)
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
        try:
            os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return self.status()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
