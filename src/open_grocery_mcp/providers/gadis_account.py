"""Hybrid Gadis account: HTTP cart reads/writes with browser checkout fallback."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import GADIS_BROWSER_CONFIG
from open_grocery_mcp.providers.gadis_cart import GadisCartMixin
from open_grocery_mcp.providers.gadis_http import GadisHTTPClient


class GadisAccountClient(GadisCartMixin):
    """Authenticated Gadis workflow split at the verified contract boundary.

    Login, saved-address selection and checkout stay in Playwright. Session
    verification, cart reads and whole-unit reversible cart mutations use the
    captured HTTP contract, with a browser fallback when HTTP is unsuitable.
    """

    def __init__(
        self,
        *,
        browser: BrowserAccountClient | None = None,
        http: GadisHTTPClient | None = None,
    ) -> None:
        self.config = GADIS_BROWSER_CONFIG
        self._browser = browser or BrowserAccountClient(self.config)
        self._owns_http = http is None
        state_path = getattr(self._browser, "state_path", None)
        self._http = http or GadisHTTPClient(state_path=state_path)

    def _reset_http_session(self) -> None:
        invalidate = getattr(self._http, "invalidate_session", None)
        if callable(invalidate):
            invalidate()
            return
        if not self._owns_http:
            return
        self._http.close()
        self._http = GadisHTTPClient(
            state_path=getattr(self._browser, "state_path", None)
        )

    def status(self) -> dict[str, Any]:
        browser = self._browser.status()
        http = self._http.status()
        return {
            **browser,
            **http,
            "authenticated_session": bool(http.get("authenticated")),
            "validated_live": bool(http.get("http_session_checked")),
            "account_backend": "gadis_http",
            "cart_backend": "gadis_http_with_browser_fallback",
            "delivery_backend": "browser",
            "checkout_backend": "browser",
        }

    def import_storage_state(self, storage_state_path: str) -> dict[str, Any]:
        imported = self._browser.import_storage_state(storage_state_path)
        self._reset_http_session()
        return {**imported, **self.status()}

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        result = self._browser.login_with_browser(timeout_seconds=timeout_seconds)
        self._reset_http_session()
        return {**result, **self.status()}

    def addresses(self) -> list[dict[str, Any]]:
        return self._browser.addresses()

    def slots(self, address_id: str | int) -> list[dict[str, Any]]:
        return self._browser.slots(address_id)

    def preview_checkout(
        self,
        *,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        return self._browser.preview_checkout(
            expected_version=expected_version,
            max_total=max_total,
        )

    def create_checkout(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        return self._browser.create_checkout(plan)

    def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        return self._browser.get_checkout(checkout_id)

    def set_checkout_delivery(
        self,
        checkout_id: str,
        *,
        address_id: str | int,
        slot_id: str,
        max_total: Decimal,
    ) -> dict[str, Any]:
        return self._browser.set_checkout_delivery(
            checkout_id,
            address_id=address_id,
            slot_id=slot_id,
            max_total=max_total,
        )

    def submit_order(
        self,
        checkout_id: str,
        *,
        max_total: Decimal,
    ) -> dict[str, Any]:
        return self._browser.submit_order(checkout_id, max_total=max_total)

    def close(self) -> None:
        self._http.close()
        self._browser.close()


__all__ = ["GadisAccountClient"]
