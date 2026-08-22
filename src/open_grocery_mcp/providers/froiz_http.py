"""Authenticated Froiz HTTP client built from the verified Nuxt contract.

The Froiz storefront is a Nuxt SPA whose REST API lives on
``servicios.froiz.com``. Session uses an OAuth bearer token that the SPA
rotates at every boot, so this client bootstraps a fresh token by opening the
saved browser session headlessly whenever the stored one is rejected, and
caches it locally until it expires.

Verified live contract (value-free):

- ``POST /api/cart`` with ``{"items": [...]}`` creates a cart (201);
- ``PUT /api/cart/{id}`` replaces the full reviewed cart object;
- ``GET /api/cart/raw/{id}`` reads the current cart;
- ``DELETE /api/cart/{id}`` disposes of a whole cart;
- the user's active cart id comes from ``/api/me``
  (``userChannelOptions[channelName == "shop"].cartId``).

Order submission (`/api/orders`) and payment (`/api/payment/*`) are never
called by this client and stay behind separate guarded workflows.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote

import httpx

from open_grocery_mcp.errors import AuthenticationRequired, ProviderError

_API_BASE = "https://servicios.froiz.com"
_SITE_BASE = "https://supermercado.froiz.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_TOKEN_MAX_AGE_SECONDS = 8 * 3600


def _default_state_path() -> Path:
    configured = os.getenv("OPEN_GROCERY_FROIZ_STATE_PATH")
    if configured:
        return Path(configured).expanduser()
    root = Path(
        os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")
    ).expanduser()
    return root / "froiz" / "storage_state.json"


def _default_token_cache_path() -> Path:
    root = Path(
        os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")
    ).expanduser()
    return root / "froiz" / "http_token.json"


class FroizHTTPClient:
    """Read-write authenticated Froiz cart client over the Nuxt REST API."""

    def __init__(
        self,
        *,
        state_path: str | os.PathLike[str] | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
        token_cache_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else _default_state_path()
        self._token_cache_path = (
            Path(token_cache_path).expanduser()
            if token_cache_path
            else _default_token_cache_path()
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
                "Origin": _SITE_BASE,
                "Referer": _SITE_BASE + "/",
            },
        )
        self._access_token: str | None = None
        self._bootstrap_lock = threading.Lock()

    # ------------------------------------------------------------------ auth

    def _stored_token(self) -> str | None:
        try:
            payload = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        token = str(payload.get("token") or "").strip()
        fetched_at = float(payload.get("fetched_at") or 0)
        if not token or time.time() - fetched_at > _TOKEN_MAX_AGE_SECONDS:
            return None
        return token

    def _store_token(self, token: str) -> None:
        try:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_cache_path.write_text(
                json.dumps({"token": token, "fetched_at": time.time()}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _cookie_token(self) -> str | None:
        """Return the rotated-out cookie token; kept as a last resort."""
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        for row in state.get("cookies", []) or []:
            if (
                isinstance(row, Mapping)
                and str(row.get("name")) == "auth._token.froiz"
            ):
                value = unquote(str(row.get("value", ""))).strip()
                if value.lower().startswith("bearer "):
                    value = value[7:].strip()
                return value or None
        return None

    def _bootstrap_token_via_browser(self) -> str | None:
        """Open the saved session headlessly and grab the SPA's fresh bearer.

        The storefront rotates its OAuth access token on every boot and keeps
        the new one in memory/session storage, so the only reliable source for
        a working token is the rendered session itself.
        """
        if not self.state_path.exists():
            return None
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None

        grabbed: dict[str, str | None] = {"token": None}

        def on_request(request: Any) -> None:
            if grabbed["token"] is None and "servicios.froiz.com/api/" in request.url:
                auth = request.headers.get("authorization", "")
                if auth.lower().startswith("bearer "):
                    grabbed["token"] = auth[7:].strip()

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        storage_state=str(self.state_path),
                        locale="es-ES",
                        viewport={"width": 1280, "height": 900},
                    )
                    context.on("request", on_request)
                    page = context.new_page()
                    page.set_default_timeout(45000)
                    try:
                        page.goto(_SITE_BASE + "/", wait_until="commit")
                    except Exception:
                        pass
                    page.wait_for_timeout(6000)
                    context.storage_state(path=str(self.state_path))
                finally:
                    browser.close()
        except Exception:
            return None
        return grabbed["token"] or None

    def _bearer(self) -> str:
        if self._access_token:
            return self._access_token
        with self._bootstrap_lock:
            if self._access_token:
                return self._access_token
            token = self._stored_token() or self._cookie_token()
            if token:
                self._access_token = token
            return self._access_token or ""

    def _refresh_token(self) -> str | None:
        with self._bootstrap_lock:
            self._access_token = None
            token = self._bootstrap_token_via_browser()
            if token:
                self._access_token = token
                self._store_token(token)
            return self._access_token

    def invalidate_session(self) -> None:
        self._access_token = None
        try:
            self._token_cache_path.unlink(missing_ok=True)
        except OSError:
            pass

    # --------------------------------------------------------------- requests

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        allow_refresh: bool = True,
    ) -> Any:
        url = f"{_API_BASE}{path}"
        token = self._bearer()
        headers = (
            {"Authorization": f"Bearer {token}"} if token else {}
        )
        try:
            response = self._client.request(
                method, url, json=json_body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Froiz request failed: {exc}") from exc
        if response.status_code == 401 and allow_refresh:
            refreshed = self._refresh_token()
            if refreshed:
                return self._request(
                    method, path, json_body=json_body, allow_refresh=False
                )
            raise AuthenticationRequired(
                "Froiz session is expired or invalid; run login_with_browser"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderError(
                f"Froiz {method} {path} returned HTTP {response.status_code}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Froiz {path} returned invalid JSON") from exc

    # ------------------------------------------------------------------ carts

    @staticmethod
    def stable_version(payload: Mapping[str, Any]) -> int:
        """Content-derived version: the API exposes no mutation counter."""

        def item_key(item: Mapping[str, Any]) -> list[str]:
            product = item.get("product")
            product_id = (
                str(item.get("product_id") or "")
                or str((product or {}).get("id") if isinstance(product, Mapping) else "")
            )
            return [
                product_id,
                str(item.get("qty", "")),
                str(item.get("unit", "")),
            ]

        items = sorted(
            item_key(item)
            for item in payload.get("items", [])
            if isinstance(item, Mapping)
        )
        material = json.dumps(
            {
                "items": items,
                "total": str(
                    _as_decimal(payload.get("total"), default="0").normalize()
                ),
                "count": len(items),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") >> 1

    @staticmethod
    def normalize_cart(payload: Mapping[str, Any]) -> dict[str, Any]:
        lines = []
        for item in payload.get("items", []) or []:
            if not isinstance(item, Mapping):
                continue
            product = item.get("product")
            product_map = product if isinstance(product, Mapping) else {}
            product_id = str(
                item.get("product_id") or product_map.get("id") or ""
            ).strip()
            if not product_id:
                continue
            price = product_map.get("price")
            lines.append(
                {
                    "product_id": product_id,
                    "name": str(product_map.get("name") or ""),
                    "quantity": float(
                        _as_decimal(item.get("qty"), default="0")
                    ),
                    "unit_price": float(_as_decimal(price)) if price is not None else 0.0,
                }
            )
        total = _as_decimal(payload.get("total"))
        return {
            "store": "froiz",
            "cart_id": str(payload.get("id", "")).strip() or None,
            "version": FroizHTTPClient.stable_version(payload),
            "products_count": len(lines),
            "total": float(total),
            "total_text": f"{total:.2f}",
            "currency": "EUR",
            "lines": lines,
        }

    def channel_cart_id(self) -> str | None:
        me_payload = self.me()
        options = (
            me_payload.get("userChannelOptions", [])
            if isinstance(me_payload, Mapping)
            else []
        )
        for option in options if isinstance(options, list) else []:
            if (
                isinstance(option, Mapping)
                and option.get("channelName") == "shop"
                and option.get("cartId")
            ):
                return str(option["cartId"])
        return None

    def me(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/me")
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz profile returned an invalid response")
        return payload

    def addresses(self) -> list[dict[str, Any]]:
        """Saved addresses from /api/me with personal values redacted."""
        me_payload = self.me()
        rows = me_payload.get("userAddresses", [])
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            result.append(
                {
                    "id": str(row.get("id", "")).strip() or None,
                    "is_default": bool(row.get("isDefault")),
                    "field_names": sorted(str(key) for key in row),
                }
            )
        return result

    def default_postal_code(self) -> str | None:
        for row in self.me().get("userAddresses", []) or []:
            if isinstance(row, Mapping) and row.get("isDefault"):
                code = str(row.get("postalCode") or "").strip()
                if code:
                    return code
        return None

    def store_by_postal_code(self, postal_code: str) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/api/stores/postalcode/{str(postal_code).strip()}",
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz store lookup returned an invalid response")
        return payload

    def delivery_calendar(self, postal_code: str | None = None) -> list[dict[str, Any]]:
        """Delivery slots for the store serving a postal code.

        Live contract: ``GET /api/stores/postalcode/{cp}`` yields the store's
        ``codEnt``/``codSubent`` and ``GET
        /api/deliverymatrix/calendar/{codEnt}_{codSubent}`` returns a week of
        ``deliveryCalendar`` days each holding six two-hour ``slots``.
        """
        effective = str(postal_code or "").strip()
        if not effective:
            effective = str(self.default_postal_code() or "").strip()
        if not effective:
            raise ProviderError("Froiz delivery calendar needs a postal code")
        store = self.store_by_postal_code(effective)
        code = store.get("codEnt")
        subcode = store.get("codSubent")
        if code in (None, "") or subcode in (None, ""):
            raise ProviderError("Froiz store lookup lacked codEnt/codSubent")
        payload = self._request(
            "GET", f"/api/deliverymatrix/calendar/{code}_{subcode}"
        )
        calendar = (
            payload.get("deliveryCalendar")
            if isinstance(payload, Mapping)
            else None
        )
        if calendar is None and isinstance(payload, Mapping):
            calendar = payload.get("elements")
        if not isinstance(calendar, list):
            raise ProviderError(
                "Froiz delivery calendar returned an invalid response"
            )
        slots: list[dict[str, Any]] = []
        for day in calendar:
            if not isinstance(day, Mapping):
                continue
            date = str(day.get("date", "")).strip()
            day_active = bool(day.get("active"))
            for slot in day.get("slots", []) or []:
                if not isinstance(slot, Mapping):
                    continue
                text = str(slot.get("slotText", "")).strip()
                start, _, end = text.partition(" - ")
                slots.append(
                    {
                        "id": f"{date}#{slot.get('slotNumber')}",
                        "date": date,
                        "start": start.strip() or None,
                        "end": end.strip() or None,
                        "available": bool(slot.get("active")) and day_active,
                        "active": bool(slot.get("active")),
                    }
                )
        return slots

    def raw_cart(self, cart_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/api/cart/raw/{str(cart_id).strip()}")
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz raw cart returned an invalid response")
        return payload

    def create_cart(self, items: list[Mapping[str, Any]]) -> dict[str, Any]:
        payload = self._request("POST", "/api/cart", json_body={"items": list(items)})
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz cart creation returned an invalid response")
        return payload

    def update_cart(
        self, cart_id: str, items: list[Mapping[str, Any]]
    ) -> dict[str, Any]:
        payload = self._request(
            "PUT", f"/api/cart/{str(cart_id).strip()}", json_body={"items": list(items)}
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz cart update returned an invalid response")
        return payload

    def delete_cart(self, cart_id: str) -> None:
        self._request("DELETE", f"/api/cart/{str(cart_id).strip()}")

    def status(self) -> dict[str, Any]:
        token = self._bearer()
        return {
            "store": "froiz",
            "state_path": str(self.state_path),
            "session_present": self.state_path.is_file(),
            "authenticated": bool(token),
            "http_backend": "froiz_http",
            "profile_values_exposed": False,
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _as_decimal(value: Any, *, default: str = "0"):
    from decimal import Decimal, InvalidOperation

    if value is None or isinstance(value, bool):
        return Decimal(default)
    try:
        result = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal(default)
    return result if result.is_finite() else Decimal(default)


__all__ = ["FroizHTTPClient"]
