"""Authenticated Mercadona HTTP requests and token refresh."""

from __future__ import annotations

import re
from typing import Any, Mapping

import httpx

from open_grocery_mcp.errors import AuthenticationRequired, ProviderError
from open_grocery_mcp.providers.mercadona_state import (
    _BASE_URL,
    MercadonaSession,
    _decode_jwt_payload,
)


class MercadonaHTTPMixin:

    _PRIVATE_PATH_SEGMENT = re.compile(
        r"(?i)(/(?:customers?|addresses?|carts?|checkouts?|orders?|payments?|users?|accounts?))/[^/?#]+"
    )

    @classmethod
    def _safe_request_path(cls, path: str) -> str:
        """Keep endpoint diagnostics useful without exposing private route ids."""

        return cls._PRIVATE_PATH_SEGMENT.sub(r"\1/<private>", path)

    def _refresh(self, session: MercadonaSession) -> MercadonaSession:
        if not session.refresh_token:
            raise AuthenticationRequired('Mercadona access token expired and no refresh token is stored')
        try:
            headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
            if session.cookie_header:
                headers['Cookie'] = session.cookie_header
            response = self._client.post(f'{_BASE_URL}/api/auth/tokens/', json={'refresh_token': session.refresh_token}, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise AuthenticationRequired(f'Mercadona token refresh returned HTTP {exc.response.status_code}') from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthenticationRequired(f'could not refresh Mercadona session: {exc}') from exc
        if not isinstance(payload, Mapping):
            raise AuthenticationRequired('Mercadona refresh returned an invalid response')
        access_token = str(payload.get('access_token') or payload.get('token') or '').strip()
        if not access_token:
            raise AuthenticationRequired('Mercadona refresh returned no access token')
        claims = _decode_jwt_payload(access_token)
        customer_id = str(payload.get('customer_id') or payload.get('customer_uuid') or claims.get('customer_uuid') or session.customer_id)
        refresh_token = str(payload.get('refresh_token') or session.refresh_token)
        self._write_user_tokens(access_token=access_token, refresh_token=refresh_token, customer_id=customer_id)
        return self._load_session()

    def _request(self, method: str, path: str, *, json_body: Any = None, params: Mapping[str, Any] | None = None, warehouse: str | None = None, retry_auth: bool = True) -> tuple[Any, httpx.Response]:
        with self._lock:
            method = method.upper()
            safe_path = self._safe_request_path(path)
            session = self._load_session()
            if not session.access_token_valid:
                if session.refresh_token:
                    session = self._refresh(session)
                else:
                    raise AuthenticationRequired(
                        'Mercadona access token is expired or has no verifiable expiry; '
                        'run login_with_browser'
                    )
            headers = {'Authorization': f'Bearer {session.access_token}', 'Accept': 'application/json'}
            if json_body is not None:
                headers['Content-Type'] = 'application/json'
            if session.cookie_header:
                headers['Cookie'] = session.cookie_header
            selected_warehouse = warehouse or self._warehouse
            if selected_warehouse:
                headers['x-customer-wh'] = selected_warehouse
            try:
                response = self._client.request(method, f'{_BASE_URL}/api{path}', headers=headers, json=json_body, params=params)
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f'Mercadona {method} {safe_path} transport failed '
                    f'({type(exc).__name__})',
                    operation=f'{method} {safe_path}',
                ) from exc
            discovered = response.headers.get('x-customer-wh', '').strip()
            if discovered:
                self._warehouse = discovered
            if (
                response.status_code == 401
                and retry_auth
                and session.refresh_token
                and method in {'GET', 'HEAD', 'OPTIONS'}
            ):
                self._refresh(session)
                return self._request(method, path, json_body=json_body, params=params, warehouse=warehouse, retry_auth=False)
            if response.status_code == 401:
                detail = (
                    'Mercadona rejected a write with HTTP 401; the mutation was not '
                    'retried because its remote result may be ambiguous'
                    if method not in {'GET', 'HEAD', 'OPTIONS'}
                    else 'Mercadona session is expired or invalid'
                )
                raise AuthenticationRequired(detail)
            if response.status_code < 200 or response.status_code >= 300:
                raise ProviderError(
                    f'Mercadona {method} {safe_path} returned HTTP '
                    f'{response.status_code}',
                    status_code=response.status_code,
                    operation=f'{method} {safe_path}',
                )
            if not response.content:
                return ({}, response)
            try:
                return (response.json(), response)
            except ValueError as exc:
                raise ProviderError(
                    f'Mercadona {safe_path} returned invalid JSON',
                    status_code=response.status_code,
                    operation=f'{method} {safe_path}',
                ) from exc

    def _customer_id(self) -> str:
        return self._load_session().customer_id

    def _params(self) -> dict[str, str]:
        params = {'lang': 'es'}
        if self._warehouse:
            params['wh'] = self._warehouse
        return params

    def get_customer(self) -> dict[str, Any]:
        payload, _ = self._request('GET', f'/customers/{self._customer_id()}/')
        return payload if isinstance(payload, dict) else {}
