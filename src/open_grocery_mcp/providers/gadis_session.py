"""Lightweight Gadis HTTP session verification from a Playwright state file."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

_SESSION_URL = "https://www.gadisline.com/api/auth/session"


def _default_state_path() -> Path:
    configured = os.getenv("OPEN_GROCERY_GADIS_STATE_PATH")
    if configured:
        return Path(configured).expanduser()
    root = Path(os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")).expanduser()
    return root / "gadis" / "storage_state.json"


class GadisSessionClient:
    """Verify a Gadis browser session without launching Chromium.

    The public response is deliberately value-free: it exposes only whether the
    endpoint authenticated and which user-field names were present. Cookie and
    profile values never leave this client. The retailer OAuth bearer token is
    kept in memory and is never written into a status payload.
    """

    def __init__(
        self,
        *,
        state_path: str | os.PathLike[str] | None = None,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.state_path = (
            Path(state_path).expanduser() if state_path else _default_state_path()
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "open-grocery-mcp/0.4 "
                    "(+https://github.com/PabloPC05/open-grocery-mcp)"
                ),
            },
        )

    def _cookie_jar(self) -> tuple[dict[str, str], list[str]]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}, []
        rows = state.get("cookies", []) if isinstance(state, Mapping) else []
        cookies: dict[str, str] = {}
        names: list[str] = []
        now = time.time()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            domain = str(row.get("domain", "")).casefold().lstrip(".")
            name = str(row.get("name", "")).strip()
            value = str(row.get("value", ""))
            try:
                expires = float(row.get("expires", -1))
            except (TypeError, ValueError):
                expires = -1
            if not domain.endswith("gadisline.com") or not name or not value:
                continue
            if expires > 0 and expires <= now:
                continue
            cookies[name] = value
            names.append(name)
        return cookies, sorted(set(names))

    def _cookie_header(self) -> str | None:
        cookies, _ = self._cookie_jar()
        if not cookies:
            return None
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    def session_token(self) -> tuple[str | None, str | None]:
        """Return the retailer OAuth access token and expiry, or ``(None, None)``.

        The NextAuth session exposes ``token.accessToken``, which is the Keycloak
        bearer token used by the ``catalog``/``store``/``cart``/``clients``
        microservices. The value is returned to the caller and never persisted.
        """

        cookie_header = self._cookie_header()
        if not cookie_header:
            return None, None
        try:
            response = self._client.get(
                _SESSION_URL,
                headers={"Cookie": cookie_header},
            )
        except httpx.HTTPError:
            return None, None
        if response.status_code != 200:
            return None, None
        try:
            payload = response.json()
        except ValueError:
            return None, None
        if not isinstance(payload, Mapping):
            return None, None
        token = payload.get("token")
        access_token = token.get("accessToken") if isinstance(token, Mapping) else None
        access_token = str(access_token or "").strip() or None
        expires = str(payload.get("expires") or "").strip() or None
        return access_token, expires

    def status(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "store": "gadis",
            "state_path": str(self.state_path),
            "session_present": self.state_path.is_file(),
            "http_session_checked": False,
            "authenticated": False,
        }
        cookies, names = self._cookie_jar()
        base["cookie_names"] = names
        if not cookies:
            return base
        cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        try:
            response = self._client.get(
                _SESSION_URL,
                headers={"Cookie": cookie_header},
            )
        except httpx.HTTPError as exc:
            base["error"] = f"could not verify Gadis HTTP session: {type(exc).__name__}"
            return base
        base["http_session_checked"] = True
        base["http_status"] = response.status_code
        if response.status_code in {401, 403}:
            return base
        if response.status_code < 200 or response.status_code >= 300:
            base["error"] = "Gadis session endpoint returned an unexpected status"
            return base
        try:
            payload = response.json()
        except ValueError:
            base["error"] = "Gadis session endpoint returned invalid JSON"
            return base
        if not isinstance(payload, Mapping):
            return base
        user = payload.get("user")
        user_fields = sorted(str(key) for key in user) if isinstance(user, Mapping) else []
        token = payload.get("token")
        bearer_available = bool(
            isinstance(token, Mapping) and str(token.get("accessToken") or "").strip()
        )
        authenticated = bool(user_fields or payload.get("expires"))
        base.update(
            {
                "authenticated": authenticated,
                "user_fields": user_fields,
                "expiry_present": bool(payload.get("expires")),
                "bearer_token_available": bearer_available,
                "profile_values_exposed": False,
            }
        )
        return base

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
