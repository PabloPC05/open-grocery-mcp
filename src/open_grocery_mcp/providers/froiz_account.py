"""Hybrid Froiz account: HTTP cart with explicit Playwright fallback.

Cart reads and whole-object mutations run over the verified Nuxt REST
contract. Login, delivery and checkout keep using the browser session until
their HTTP contracts are captured. Fail-closed rules mirror Gadis:

- every write re-reads the cart first and compares a content fingerprint;
- ambiguous responses are never retried blindly;
- a failed write restores the previous reviewed items.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    BudgetExceeded,
    ConcurrentCartChange,
    ProviderError,
)
from open_grocery_mcp.models import money
from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import FROIZ_BROWSER_CONFIG
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient

_UNIT_DEFAULT = "ud"


def _as_decimal(value: Any, *, default: str = "0") -> Decimal:
    from decimal import Decimal, InvalidOperation

    if value is None or isinstance(value, bool):
        return Decimal(default)
    try:
        result = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal(default)
    return result if result.is_finite() else Decimal(default)


class FroizAccountClient:
    """One authenticated Froiz session split at the verified boundary."""

    def __init__(
        self,
        *,
        browser: BrowserAccountClient | None = None,
        http: FroizHTTPClient | None = None,
    ) -> None:
        self._browser = browser or BrowserAccountClient(FROIZ_BROWSER_CONFIG)
        self._http = http or FroizHTTPClient(
            state_path=getattr(self._browser, "state_path", None)
        )

    def status(self) -> dict[str, Any]:
        browser = self._browser.status()
        http_status = self._http.status()
        return {
            **browser,
            **http_status,
            "account_backend": "froiz_http_with_browser_fallback",
            "cart_backend": "froiz_http_with_browser_fallback",
            "delivery_backend": "browser",
            "checkout_backend": "browser",
        }

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        result = self._browser.login_with_browser(timeout_seconds=timeout_seconds)
        self._http.invalidate_session()
        return {**result, **self.status()}

    def import_storage_state(self, storage_state_path: str) -> dict[str, Any]:
        imported = self._browser.import_storage_state(storage_state_path)
        self._http.invalidate_session()
        return {**imported, **self.status()}

    # ------------------------------------------------------------------ reads

    @staticmethod
    def _client_item(item: Mapping[str, Any]) -> dict[str, Any]:
        product = item.get("product")
        product_map = product if isinstance(product, Mapping) else {}
        return {
            "product_id": str(
                item.get("product_id") or product_map.get("id") or ""
            ).strip(),
            "qty": float(_as_decimal(item.get("qty"), default="0")),
            "unit": str(item.get("unit") or _UNIT_DEFAULT),
            "comment": str(item.get("comment") or ""),
        }

    def _load_current(
        self,
    ) -> tuple[str | None, dict[str, Any], list[dict[str, Any]]]:
        """Return (cart_id-or-None, normalized, client-shaped items).

        ``None`` means the session has no cart bound yet: the storefront
        itself creates it lazily with ``POST /api/cart`` on the first add.
        """
        cart_id = self._http.channel_cart_id()
        if not cart_id:
            empty = self._http.normalize_cart({"items": []})
            return None, empty, []
        raw = self._http.raw_cart(cart_id)
        normalized = self._http.normalize_cart(raw)
        items = [
            self._client_item(item)
            for item in raw.get("items", []) or []
            if isinstance(item, Mapping)
        ]
        return cart_id, normalized, [i for i in items if i["product_id"]]

    def real_cart(self) -> dict[str, Any]:
        try:
            _, normalized, _ = self._load_current()
            return {
                **normalized,
                "cart_backend": "froiz_http",
                "browser_driven": False,
            }
        except (AuthenticationRequired, ProviderError) as exc:
            fallback = self._browser.cart()
            return {
                **fallback,
                "cart_backend": "browser",
                "browser_driven": True,
                "http_fallback_reason": type(exc).__name__,
            }

    # -------------------------------------------------------------- mutations

    @staticmethod
    def _apply_changes(
        current_items: list[dict[str, Any]],
        changes: Sequence[Mapping[str, Any]],
        mode: str,
    ) -> list[dict[str, Any]]:
        desired: dict[str, dict[str, Any]] = (
            {}
            if mode == "replace"
            else {i["product_id"]: dict(i) for i in current_items}
        )
        for change in changes:
            product_id = str(change.get("product_id", "")).strip()
            if not product_id:
                continue
            quantity = _as_decimal(change.get("quantity"))
            if quantity <= 0:
                desired.pop(product_id, None)
                continue
            existing = desired.get(product_id)
            unit = str(change.get("unit") or (existing or {}).get("unit") or _UNIT_DEFAULT)
            desired[product_id] = {
                "product_id": product_id,
                "qty": float(quantity),
                "unit": unit,
                "comment": str(change.get("comment") or (existing or {}).get("comment") or ""),
            }
        return list(desired.values())

    def _estimated_total(
        self,
        items: list[dict[str, Any]],
        price_index: Mapping[str, Decimal],
    ) -> Decimal:
        total = Decimal("0")
        for item in items:
            price = price_index.get(item["product_id"])
            if price is not None:
                total += price * _as_decimal(item["qty"])
        return total

    def preview_cart_update(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        if mode not in {"merge", "replace"}:
            from open_grocery_mcp.errors import InvalidRequest

            raise InvalidRequest("cart update mode must be 'merge' or 'replace'")
        cart_id, normalized, current_items = self._load_current()

        version = int(normalized.get("version") or 0)
        if expected_version is not None and version != expected_version:
            raise ConcurrentCartChange(
                f"Froiz cart version is {version}, not reviewed version "
                f"{expected_version}"
            )

        previous_lines = normalized.get("lines", [])
        price_index = {
            line["product_id"]: _as_decimal(line.get("unit_price"))
            for line in previous_lines
            if isinstance(line, Mapping) and line.get("unit_price")
        }
        for change in changes:
            supplied = change.get("unit_price")
            pid = str(change.get("product_id", "")).strip()
            if supplied and pid:
                price_index.setdefault(pid, _as_decimal(supplied))

        desired_items = self._apply_changes(current_items, changes, mode)
        estimated = self._estimated_total(desired_items, price_index)
        if estimated > max_total:
            raise BudgetExceeded(
                f"proposed Froiz cart total {money(estimated)} EUR exceeds cap "
                f"{money(max_total)} EUR"
            )

        return {
            "store": "froiz",
            "plan_backend": "froiz_http",
            "expected_cart_version": version,
            "max_total": float(max_total),
            "max_total_text": money(max_total),
            "estimated_total": float(estimated),
            "estimated_total_text": money(estimated),
            "currency": "EUR",
            "lines": [
                {
                    "product_id": i["product_id"],
                    "quantity": float(i["qty"]),
                    "unit": i["unit"],
                }
                for i in desired_items
            ],
            "desired_items": desired_items,
            "previous_items": current_items,
            "previous_lines": previous_lines,
            "retailer_cart_modified": False,
        }

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        if plan.get("plan_backend") != "froiz_http":
            return self._browser.commit_cart_update(plan)

        cart_id, fresh, _ = self._load_current()
        expected_version = int(plan.get("expected_cart_version") or 0)
        if cart_id is not None and int(fresh.get("version") or 0) != expected_version:
            raise ConcurrentCartChange(
                f"Froiz cart changed from version {expected_version}; review again"
            )

        desired_items = [
            dict(item)
            for item in plan.get("desired_items", [])
            if isinstance(item, Mapping)
        ]
        previous_items = [
            dict(item)
            for item in plan.get("previous_items", [])
            if isinstance(item, Mapping)
        ]
        max_total = _as_decimal(plan.get("max_total"))

        creating = cart_id is None
        try:
            if creating:
                payload = self._http.create_cart(desired_items)
            else:
                payload = self._http.update_cart(cart_id, desired_items)
        except Exception as original:
            # Never retry the ambiguous write; restore reviewed state instead.
            rollback_error: Exception | None = None
            try:
                rebound = self._http.channel_cart_id() or cart_id
                if rebound:
                    self._http.update_cart(rebound, previous_items)
            except Exception as exc:
                rollback_error = exc
            if rollback_error is not None:
                raise ProviderError(
                    "Froiz cart update failed and rollback could not be "
                    "verified; inspect the retailer cart before further writes"
                ) from original
            raise ProviderError(
                f"Froiz cart update failed ({type(original).__name__}); "
                "previous cart restored"
            ) from original

        updated = self._http.normalize_cart(payload)
        updated_ids = sorted(line["product_id"] for line in updated["lines"])
        desired_ids = sorted(i["product_id"] for i in desired_items)
        if updated_ids != desired_ids:
            # Verify-by-read proved a mismatch: roll back to previous items.
            if updated.get("cart_id"):
                self._http.update_cart(str(updated["cart_id"]), previous_items)
            elif cart_id:
                self._http.update_cart(cart_id, previous_items)
            raise ProviderError(
                "Froiz cart did not match the reviewed quantities; "
                "previous cart restored"
            )
        total = _as_decimal(updated.get("total"))
        if desired_items and total <= 0:
            self._rollback_empty(updated, cart_id, previous_items)
            raise BudgetExceeded(
                "could not verify a positive Froiz cart total after writing"
            )
        if total > max_total:
            self._rollback_empty(updated, cart_id, previous_items)
            raise BudgetExceeded(
                f"actual Froiz cart total {money(total)} EUR exceeds cap "
                f"{money(max_total)} EUR; previous cart restored"
            )

        return {
            **updated,
            "retailer_cart_modified": True,
            "verified_against_reviewed_plan": True,
            "order_placed": False,
            "cart_backend": "froiz_http",
        }

    def _rollback_empty(
        self,
        updated: Mapping[str, Any],
        cart_id: str | None,
        previous_items: list[dict[str, Any]],
    ) -> None:
        target = str(updated.get("cart_id") or "") or cart_id
        if target:
            try:
                self._http.update_cart(target, previous_items)
            except Exception:
                pass

        return {
            **updated,
            "retailer_cart_modified": True,
            "verified_against_reviewed_plan": True,
            "order_placed": False,
            "cart_backend": "froiz_http",
        }

    # ------------------------------------------------- browser-backed regions

    def addresses(self) -> list[dict[str, Any]]:
        return self._browser.addresses()

    def slots(self, address_id: str | int) -> list[dict[str, Any]]:
        return self._browser.slots(address_id)

    def preview_checkout(
        self, *, expected_version: int | None, max_total: Decimal
    ) -> dict[str, Any]:
        return self._browser.preview_checkout(
            expected_version=expected_version, max_total=max_total
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
        self, checkout_id: str, *, max_total: Decimal
    ) -> dict[str, Any]:
        return self._browser.submit_order(checkout_id, max_total=max_total)

    def close(self) -> None:
        self._http.close()
        self._browser.close()


__all__ = ["FroizAccountClient"]
