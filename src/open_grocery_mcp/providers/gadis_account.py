"""Hybrid Gadis account: HTTP cart reads/writes with browser checkout fallback."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    ProviderError,
)
from open_grocery_mcp.models import as_decimal, money
from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import GADIS_BROWSER_CONFIG
from open_grocery_mcp.providers.browser_normalize import same_line_identity
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
            "delivery_backend": "gadis_http_with_browser_fallback",
            "checkout_backend": "gadis_http_with_browser_fallback",
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
        try:
            cart = self._http_cart()
            cart_id = str(cart.get("cart_id") or "").strip()
            if cart_id:
                rows = self._http.addresses(cart_id)
                if rows:
                    return rows
        except (AuthenticationRequired, ProviderError):
            pass
        return self._browser.addresses()

    def slots(self, address_id: str | int) -> list[dict[str, Any]]:
        try:
            return self._http.delivery_slots()
        except (AuthenticationRequired, ProviderError):
            return self._browser.slots(address_id)

    @staticmethod
    def _cart_lines(cart: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        lines = cart.get("lines", [])
        if not isinstance(lines, list):
            return []
        return [line for line in lines if isinstance(line, Mapping)]

    @classmethod
    def _carts_equivalent(
        cls,
        reviewed: Mapping[str, Any],
        browser_cart: Mapping[str, Any],
    ) -> bool:
        """Match HTTP and browser carts without assuming equal version schemes."""

        left = cls._cart_lines(reviewed)
        right = cls._cart_lines(browser_cart)
        if len(left) != len(right):
            return False
        unmatched = list(right)
        for expected in left:
            match_index: int | None = None
            for index, actual in enumerate(unmatched):
                expected_id = str(
                    expected.get("product_id") or expected.get("id") or ""
                ).strip()
                actual_id = str(
                    actual.get("product_id") or actual.get("id") or ""
                ).strip()
                if expected_id and actual_id and expected_id != actual_id:
                    continue
                if not same_line_identity(expected, actual):
                    continue
                if as_decimal(expected.get("quantity")) != as_decimal(
                    actual.get("quantity")
                ):
                    continue
                match_index = index
                break
            if match_index is None:
                return False
            unmatched.pop(match_index)
        return not unmatched

    def preview_checkout(
        self,
        *,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        if max_total <= 0:
            raise InvalidRequest("max_total must be greater than zero")
        cart = self.cart()
        version = int(cart.get("version") or 0)
        if expected_version is not None and version != expected_version:
            raise ConcurrentCartChange("Gadis cart changed after review")
        total = as_decimal(cart.get("total"))
        if total <= 0:
            raise InvalidRequest("cart is empty or has no verifiable positive total")
        if total > max_total:
            raise BudgetExceeded(
                f"Gadis cart total {money(total)} EUR exceeds cap "
                f"{money(max_total)} EUR"
            )
        backend = str(cart.get("cart_backend") or "browser")
        return {
            "store": "gadis",
            "expected_cart_version": version,
            "reviewed_cart_backend": backend,
            "max_total": float(max_total),
            "max_total_text": money(max_total),
            "cart": cart,
            "state_changed": False,
            "browser_driven": True,
        }

    def create_checkout(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        backend = str(plan.get("reviewed_cart_backend") or "browser")
        delivery = plan.get("delivery")
        if backend == "gadis_http" and isinstance(delivery, Mapping):
            return self._create_http_checkout(plan, delivery)
        if backend != "gadis_http":
            return self._browser.create_checkout(plan)

        # The HTTP contract and rendered browser cart use different version
        # schemes. First revalidate the authoritative HTTP version reviewed by
        # the user, then prove the browser sees the same lines and total before
        # translating the plan to the browser's short-lived version number.
        current_http = self._http_cart()
        expected_http_version = int(plan.get("expected_cart_version") or 0)
        current_http_version = int(current_http.get("version") or 0)
        if current_http_version != expected_http_version:
            raise ConcurrentCartChange(
                "Gadis cart changed after checkout review; prepare checkout again"
            )

        cap = as_decimal(plan.get("max_total"))
        http_total = as_decimal(current_http.get("total"))
        if http_total <= 0:
            raise InvalidRequest("Gadis cart has no verifiable positive total")
        if http_total > cap:
            raise BudgetExceeded(
                f"Gadis cart total {money(http_total)} EUR exceeds cap "
                f"{money(cap)} EUR"
            )

        browser_cart = self._browser.cart()
        browser_total = as_decimal(browser_cart.get("total"))
        if not self._carts_equivalent(current_http, browser_cart):
            raise ConcurrentCartChange(
                "Gadis browser cart does not match the reviewed HTTP cart"
            )
        if browser_total != http_total:
            raise ConcurrentCartChange(
                "Gadis browser total does not match the reviewed HTTP total"
            )

        translated_plan = {
            **dict(plan),
            "expected_cart_version": int(browser_cart.get("version") or 0),
            "cart": browser_cart,
            "reviewed_http_cart_version": expected_http_version,
        }
        result = self._browser.create_checkout(translated_plan)
        checkout_total = as_decimal(result.get("total"))
        if checkout_total <= 0:
            raise ProviderError("Gadis checkout returned no verifiable positive total")
        if checkout_total > cap:
            raise BudgetExceeded(
                f"Gadis checkout total {money(checkout_total)} EUR exceeds cap "
                f"{money(cap)} EUR"
            )
        return {
            **result,
            "reviewed_cart_backend": "gadis_http",
            "checkout_backend": "browser",
        }

    def _create_http_checkout(
        self,
        plan: Mapping[str, Any],
        delivery: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create the checkout over authenticated HTTP; never submit an order.

        Fail-closed order of operations:
        1. re-read the cart and require the reviewed version and total;
        2. validate the chosen slot against the live calendar;
        3. attach the schedule (reversible);
        4. create the checkout once, rolling the schedule back on failure.
        """
        current = self._http_cart()
        expected_version = int(plan.get("expected_cart_version") or 0)
        if int(current.get("version") or 0) != expected_version:
            raise ConcurrentCartChange(
                "Gadis cart changed after checkout review; prepare checkout again"
            )
        cap = as_decimal(plan.get("max_total"))
        total = as_decimal(current.get("total"))
        if total <= 0:
            raise InvalidRequest("Gadis cart has no verifiable positive total")
        if total > cap:
            raise BudgetExceeded(
                f"Gadis cart total {money(total)} EUR exceeds cap "
                f"{money(cap)} EUR"
            )

        cart_id = str(current.get("cart_id") or "").strip()
        store_id = str(current.get("store_id") or "").strip() or str(
            self._http._bootstrap()[1]
        )
        if not cart_id:
            raise ProviderError("Gadis cart did not expose a cart id")

        delivery_date = str(delivery.get("delivery_date", "")).strip()
        schedule_range_id = delivery.get("schedule_range_id")
        shipping_address_id = delivery.get("shipping_address_id")
        shipping_address_owner = delivery.get("shipping_address_owner")
        if not delivery_date or schedule_range_id in (None, ""):
            raise InvalidRequest(
                "Gadis HTTP checkout needs a delivery date and schedule range"
            )
        if shipping_address_id in (None, ""):
            raise InvalidRequest(
                "Gadis HTTP checkout needs a reviewed shipping address id"
            )

        slots = self._http.delivery_slots(store_id=store_id)
        selected = next(
            (
                slot
                for slot in slots
                if str(slot.get("id")) == str(schedule_range_id)
            ),
            None,
        )
        if selected is None or not selected.get("available"):
            raise InvalidRequest("selected delivery slot is not currently available")

        self._http.update_schedule(
            cart_id,
            store_id,
            delivery_date=delivery_date,
            schedule_range_id=schedule_range_id,
        )
        try:
            result = self._http.create_checkout(
                cart_id,
                store_id,
                shipping_address_id=str(shipping_address_id),
                shipping_address_owner=(
                    str(shipping_address_owner) if shipping_address_owner else None
                ),
                delivery_date=delivery_date,
                schedule_range_id=schedule_range_id,
            )
        except Exception:
            # The checkout was not created; undo the reversible schedule write.
            try:
                self._http.delete_schedule(cart_id)
            except Exception:
                pass
            raise

        removed = result.get("removed_products")
        return {
            **result,
            "reviewed_cart_backend": "gadis_http",
            "checkout_backend": "gadis_http",
            "removed_products_count": len(removed) if isinstance(removed, list) else 0,
            "max_total": float(cap),
            "max_total_text": money(cap),
        }

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
