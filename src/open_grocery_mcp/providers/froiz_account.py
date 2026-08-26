"""Hybrid Froiz account: HTTP cart with explicit Playwright fallback.

Cart reads, whole-object mutations and delivery run over the verified Nuxt
REST contract, with a browser fallback for authenticated storefront cases.
Checkout is unavailable because the observed ``orders/create`` boundary places
the real order. Fail-closed rules mirror Gadis:

- every write re-reads the cart first and compares a content fingerprint;
- ambiguous responses are never retried blindly;
- an ambiguous write is never retried or overwritten; only a newly created,
  disposable cart may be deleted after its identity is verified.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import threading
import time
from typing import Any, Mapping, Sequence

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    ProviderError,
    UnsupportedOperation,
)
from open_grocery_mcp.models import money
from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import FROIZ_BROWSER_CONFIG
from open_grocery_mcp.providers.browser_normalize import is_restricted_product
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient

_UNIT_DEFAULT = "ud"
_CATALOGUE_STORE_TTL_SECONDS = 15 * 60


def _as_decimal(value: Any, *, default: str = "0") -> Decimal:
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
        self._catalogue_store_cache: dict[str, tuple[float, str]] = {}
        self._catalogue_store_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        browser = self._browser.status()
        http_status = self._http.status()
        return {
            **browser,
            **http_status,
            "account_backend": "froiz_http_with_browser_fallback",
            "cart_backend": "froiz_http_with_browser_fallback",
            "delivery_backend": "froiz_http_with_browser_fallback",
            "checkout_backend": "browser_blocked_by_design",
            "validated_live": bool(http_status.get("authenticated")),
        }

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        result = self._browser.login_with_browser(timeout_seconds=timeout_seconds)
        self._http.invalidate_session()
        self._catalogue_store_cache.clear()
        return {**result, **self.status()}

    def import_storage_state(self, storage_state_path: str) -> dict[str, Any]:
        imported = self._browser.import_storage_state(storage_state_path)
        self._http.invalidate_session()
        self._catalogue_store_cache.clear()
        return {**imported, **self.status()}

    def open_human_review(
        self,
        *,
        checkout_id: str | None = None,
        checkout_review: bool = False,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        return self._browser.open_human_review(
            checkout_id=checkout_id,
            checkout_review=checkout_review,
            timeout_seconds=timeout_seconds,
        )

    # ------------------------------------------------------------------ reads

    @staticmethod
    def _client_item(item: Mapping[str, Any]) -> dict[str, Any]:
        product = item.get("product")
        product_map = product if isinstance(product, Mapping) else {}
        result = {
            "product_id": str(
                item.get("product_id") or product_map.get("id") or ""
            ).strip(),
            "qty": float(_as_decimal(item.get("qty"), default="0")),
            "unit": str(item.get("unit") or _UNIT_DEFAULT),
            "comment": str(item.get("comment") or ""),
        }
        # Froiz's whole-object cart contract may include optional raw fields
        # that are not needed for the public cart model.  Preserve the known
        # ``units`` field so a merge/update does not silently drop it.
        if item.get("units") is not None:
            result["units"] = item["units"]
        return result

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
        raw = self._http.processed_cart(cart_id)
        normalized = self._http.normalize_cart(raw)
        items = [
            self._client_item(item)
            for item in raw.get("items", []) or []
            if isinstance(item, Mapping)
        ]
        return cart_id, normalized, [
            i
            for i in items
            if i["product_id"] and _as_decimal(i.get("qty")) > 0
        ]

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
    def _validated_quantity(value: Any, *, product_id: str) -> Decimal:
        if isinstance(value, bool):
            raise InvalidRequest(f"invalid quantity for Froiz product {product_id!r}")
        try:
            quantity = Decimal(str(value).replace(",", ".").strip())
        except (InvalidOperation, ValueError, AttributeError):
            raise InvalidRequest(
                f"invalid quantity for Froiz product {product_id!r}"
            ) from None
        if not quantity.is_finite() or quantity < 0:
            raise InvalidRequest(f"invalid quantity for Froiz product {product_id!r}")
        if quantity > 1000:
            raise InvalidRequest(
                f"quantity for Froiz product {product_id!r} exceeds the safety limit"
            )
        return quantity

    @staticmethod
    def _item_signature(
        items: Sequence[Mapping[str, Any]],
    ) -> tuple[tuple[str, str, str, str, str], ...]:
        return tuple(
            sorted(
                (
                    str(item.get("product_id") or "").strip(),
                    str(_as_decimal(item.get("qty")).normalize()),
                    str(item.get("unit") or _UNIT_DEFAULT),
                    str(item.get("comment") or ""),
                    repr(item.get("units")) if item.get("units") is not None else "<absent>",
                )
                for item in items
                if str(item.get("product_id") or "").strip()
                and _as_decimal(item.get("qty")) > 0
            )
        )

    @classmethod
    def _apply_changes(
        cls,
        current_items: list[dict[str, Any]],
        changes: Sequence[Mapping[str, Any]],
        mode: str,
    ) -> list[dict[str, Any]]:
        current_by_id = {i["product_id"]: dict(i) for i in current_items}
        desired: dict[str, dict[str, Any]] = (
            {}
            if mode == "replace"
            else dict(current_by_id)
        )
        seen: set[str] = set()
        for change in changes:
            product_id = str(change.get("product_id", "")).strip()
            if not product_id:
                raise InvalidRequest("every Froiz cart change needs a product_id")
            if product_id in seen:
                raise InvalidRequest(
                    f"duplicate Froiz cart change for product {product_id!r}"
                )
            seen.add(product_id)
            if "quantity" not in change:
                raise InvalidRequest(
                    f"every Froiz cart change needs quantity for {product_id!r}"
                )
            quantity = cls._validated_quantity(
                change.get("quantity"), product_id=product_id
            )
            name = str(change.get("name") or "").strip()
            category = str(change.get("category") or "").strip()
            if is_restricted_product(name, category):
                raise InvalidRequest(
                    f"automated purchase of age-restricted product {name!r} is not supported"
                )
            if quantity <= 0:
                desired.pop(product_id, None)
                continue
            # Even in replace mode, a retained product may carry raw fields
            # that the PUT contract expects.  Use the reviewed current line
            # as the source for those optional fields.
            existing = desired.get(product_id) or current_by_id.get(product_id)
            unit = str(change.get("unit") or (existing or {}).get("unit") or _UNIT_DEFAULT)
            desired[product_id] = {
                "product_id": product_id,
                "qty": float(quantity),
                "unit": unit,
                "comment": str(change.get("comment") or (existing or {}).get("comment") or ""),
            }
            if existing is not None and existing.get("units") is not None:
                desired[product_id]["units"] = existing["units"]
            elif change.get("units") is not None:
                desired[product_id]["units"] = change["units"]
        if len(desired) > 100:
            raise InvalidRequest("resulting Froiz cart exceeds the 100-line safety limit")
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
            raise InvalidRequest("cart update mode must be 'merge' or 'replace'")
        if max_total <= 0:
            raise InvalidRequest("max_total must be greater than zero")
        if len(changes) > 100:
            raise InvalidRequest("Froiz cart updates are limited to 100 product lines")
        try:
            cart_id, normalized, current_items = self._load_current()
        except (AuthenticationRequired, ProviderError) as exc:
            plan = self._browser.preview_cart_update(
                changes,
                mode=mode,
                expected_version=expected_version,
                max_total=max_total,
            )
            return {
                **plan,
                "plan_backend": "browser",
                "browser_driven": True,
                "http_fallback_reason": type(exc).__name__,
            }

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
            if supplied is not None and pid and pid not in price_index:
                price = _as_decimal(supplied)
                if price <= 0:
                    raise InvalidRequest(
                        f"Froiz product {pid!r} needs a positive reviewed unit price"
                    )
                price_index[pid] = price

        desired_items = self._apply_changes(current_items, changes, mode)
        desired_ids = {item["product_id"] for item in desired_items}
        retained_restricted = next(
            (
                line
                for line in previous_lines
                if isinstance(line, Mapping)
                and str(line.get("product_id") or "") in desired_ids
                and is_restricted_product(line.get("name"))
            ),
            None,
        )
        if retained_restricted is not None:
            raise InvalidRequest(
                "automated Froiz cart changes cannot retain age-restricted products"
            )
        missing_prices = sorted(
            item["product_id"]
            for item in desired_items
            if price_index.get(item["product_id"], Decimal("0")) <= 0
        )
        if missing_prices:
            raise InvalidRequest(
                "Froiz cannot estimate the reviewed cart without positive prices "
                f"for: {', '.join(missing_prices)}"
            )
        estimated = self._estimated_total(desired_items, price_index)
        if estimated > max_total:
            raise BudgetExceeded(
                f"proposed Froiz cart total {money(estimated)} EUR exceeds cap "
                f"{money(max_total)} EUR"
            )

        return {
            "store": "froiz",
            "plan_backend": "froiz_http",
            "expected_cart_id": cart_id,
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
            "reviewed_unit_prices": {
                item["product_id"]: float(price_index[item["product_id"]])
                for item in desired_items
            },
            "previous_unit_prices": {
                str(line.get("product_id")): float(
                    _as_decimal(line.get("unit_price"))
                )
                for line in previous_lines
                if isinstance(line, Mapping) and line.get("product_id")
            },
            "previous_total": float(_as_decimal(normalized.get("subtotal"))),
            "retailer_cart_modified": False,
        }

    def _verified_http_result(
        self,
        cart_id: str,
        desired_items: Sequence[Mapping[str, Any]],
        *,
        max_total: Decimal,
        expected_total: Decimal,
        expected_prices: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw = self._http.processed_cart(cart_id)
        normalized = self._http.normalize_cart(raw)
        actual_items = [
            self._client_item(item)
            for item in raw.get("items", []) or []
            if isinstance(item, Mapping)
        ]
        if str(normalized.get("cart_id") or "") != str(cart_id):
            raise ProviderError("Froiz cart identity changed after the mutation")
        if self._item_signature(actual_items) != self._item_signature(desired_items):
            raise ProviderError(
                "Froiz cart did not match the reviewed product quantities"
            )
        subtotal = _as_decimal(normalized.get("subtotal"))
        if desired_items and subtotal <= 0:
            raise ProviderError(
                "could not verify a positive Froiz cart subtotal after writing"
            )
        if subtotal > max_total:
            raise BudgetExceeded(
                f"actual Froiz cart subtotal {money(subtotal)} EUR exceeds cap "
                f"{money(max_total)} EUR"
            )
        if subtotal.quantize(Decimal("0.01")) != expected_total.quantize(
            Decimal("0.01")
        ):
            raise ProviderError(
                "Froiz actual cart subtotal differs from the reviewed total"
            )
        calculated = Decimal("0")
        lines = normalized.get("lines", [])
        if not isinstance(lines, list):
            raise ProviderError("Froiz cart lines were not verifiable")
        actual_prices: dict[str, Decimal] = {}
        for line in lines:
            if not isinstance(line, Mapping):
                raise ProviderError("Froiz cart contained an invalid line")
            price = _as_decimal(line.get("unit_price"))
            quantity = _as_decimal(line.get("quantity"))
            if price <= 0 or quantity <= 0:
                raise ProviderError("Froiz cart contained an unverifiable price")
            product_id = str(line.get("product_id") or "").strip()
            if not product_id or product_id in actual_prices:
                raise ProviderError("Froiz cart contained an invalid product identity")
            actual_prices[product_id] = price.quantize(Decimal("0.01"))
            calculated += price * quantity
        reviewed_prices = {
            str(product_id): _as_decimal(price).quantize(Decimal("0.01"))
            for product_id, price in expected_prices.items()
        }
        if actual_prices != reviewed_prices:
            raise ProviderError(
                "Froiz cart prices did not match the reviewed unit prices"
            )
        if calculated.quantize(Decimal("0.01")) != subtotal.quantize(
            Decimal("0.01")
        ):
            raise ProviderError("Froiz cart subtotal did not match its line prices")
        return normalized

    def _discard_created_cart(self, cart_id: str) -> None:
        self._http.delete_cart(cart_id)
        if self._http.channel_cart_id() == cart_id:
            raise ProviderError("Froiz disposable cart deletion was not confirmed")
        try:
            self._http.raw_cart(cart_id)
        except ProviderError as exc:
            message = str(exc)
            if "HTTP 404" in message or "HTTP 410" in message:
                return
            raise
        raise ProviderError("Froiz disposable cart still exists after deletion")

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        if plan.get("plan_backend") != "froiz_http":
            return self._browser.commit_cart_update(plan)

        cart_id, fresh, fresh_items = self._load_current()
        expected_version = int(plan.get("expected_cart_version") or 0)
        expected_cart_id = plan.get("expected_cart_id")
        if cart_id != expected_cart_id:
            raise ConcurrentCartChange("Froiz cart identity changed; review again")
        if int(fresh.get("version") or 0) != expected_version:
            raise ConcurrentCartChange(
                f"Froiz cart changed from version {expected_version}; review again"
            )

        desired_value = plan.get("desired_items")
        previous_value = plan.get("previous_items")
        if not isinstance(desired_value, list) or not isinstance(previous_value, list):
            raise InvalidRequest("Froiz cart plan contains invalid item collections")
        if any(not isinstance(item, Mapping) for item in desired_value) or any(
            not isinstance(item, Mapping) for item in previous_value
        ):
            raise InvalidRequest("Froiz cart plan contains malformed items")
        desired_items = [dict(item) for item in desired_value]
        previous_items = [dict(item) for item in previous_value]
        if len(desired_items) > 100 or len(previous_items) > 100:
            raise InvalidRequest("Froiz cart plan exceeds the 100-line safety limit")
        for label, items in (
            ("desired", desired_items),
            ("previous", previous_items),
        ):
            seen: set[str] = set()
            for item in items:
                product_id = str(item.get("product_id") or "").strip()
                quantity = self._validated_quantity(
                    item.get("qty"), product_id=product_id
                )
                if (
                    not product_id
                    or quantity <= 0
                    or not str(item.get("unit") or "").strip()
                    or product_id in seen
                ):
                    raise InvalidRequest(
                        f"Froiz cart plan contains an invalid {label} item"
                    )
                seen.add(product_id)
        max_total = _as_decimal(plan.get("max_total"))
        expected_total = _as_decimal(plan.get("estimated_total"))
        previous_total = _as_decimal(plan.get("previous_total"))
        reviewed_prices = plan.get("reviewed_unit_prices")
        previous_prices = plan.get("previous_unit_prices")
        if not isinstance(reviewed_prices, Mapping) or not isinstance(
            previous_prices, Mapping
        ):
            raise InvalidRequest("Froiz cart plan contains invalid reviewed prices")
        calculated_total = sum(
            (
                _as_decimal(reviewed_prices.get(item["product_id"]))
                * _as_decimal(item.get("qty"))
                for item in desired_items
            ),
            Decimal("0"),
        )
        if (
            max_total <= 0
            or expected_total < 0
            or previous_total < 0
            or any(
                _as_decimal(reviewed_prices.get(item["product_id"])) <= 0
                for item in desired_items
            )
            or any(
                _as_decimal(previous_prices.get(item["product_id"])) <= 0
                for item in previous_items
            )
            or calculated_total.quantize(Decimal("0.01"))
            != expected_total.quantize(Decimal("0.01"))
        ):
            raise InvalidRequest("invalid Froiz reviewed cart limits")
        if self._item_signature(fresh_items) != self._item_signature(previous_items):
            raise ConcurrentCartChange("Froiz cart contents changed; review again")

        creating = cart_id is None
        target_cart_id: str | None = cart_id
        try:
            if creating:
                payload = self._http.create_cart(desired_items)
            else:
                payload = self._http.update_cart(cart_id, desired_items)
            target_cart_id = str(payload.get("id") or cart_id or "").strip() or None
            if not target_cart_id:
                raise ProviderError("Froiz cart mutation returned no cart id")
            updated = self._verified_http_result(
                target_cart_id,
                desired_items,
                max_total=max_total,
                expected_total=expected_total,
                expected_prices=reviewed_prices,
            )
        except Exception as original:
            # Never retry an ambiguous write. A safe read may prove that it
            # completed. An existing cart in any third state could contain a
            # concurrent user change, so it must never be overwritten here.
            try:
                observed_id = target_cart_id or self._http.channel_cart_id()
                if observed_id:
                    observed = self._verified_http_result(
                        observed_id,
                        desired_items,
                        max_total=max_total,
                        expected_total=expected_total,
                        expected_prices=reviewed_prices,
                    )
                    return {
                        **observed,
                        "retailer_cart_modified": True,
                        "verified_against_reviewed_plan": True,
                        "write_response_ambiguous_but_state_verified": True,
                        "order_placed": False,
                        "cart_backend": "froiz_http",
                    }
            except Exception:
                pass
            if not creating and target_cart_id:
                unchanged = False
                try:
                    self._verified_http_result(
                        target_cart_id,
                        previous_items,
                        max_total=max(max_total, previous_total),
                        expected_total=previous_total,
                        expected_prices=previous_prices,
                    )
                    unchanged = True
                except Exception:
                    pass
                if unchanged:
                    raise ProviderError(
                        f"Froiz cart update failed ({type(original).__name__}); "
                        "the previous cart remained unchanged"
                    ) from original
                raise ProviderError(
                    "Froiz cart update produced an unknown state; inspect the "
                    "retailer cart before further writes"
                ) from original
            try:
                if target_cart_id:
                    first_guard = self._http.raw_cart(target_cart_id)
                    second_guard = self._http.raw_cart(target_cart_id)
                    if FroizHTTPClient.stable_version(
                        first_guard
                    ) != FroizHTTPClient.stable_version(second_guard):
                        raise ConcurrentCartChange(
                            "Froiz cart changed again before rollback"
                        )
                if creating:
                    if not target_cart_id:
                        raise ProviderError(
                            "ambiguous Froiz cart creation has no safe rollback target"
                        )
                    self._discard_created_cart(target_cart_id)
                else:
                    raise ProviderError("Froiz rollback has no cart identity")
            except Exception as rollback_error:
                raise ProviderError(
                    "Froiz cart update failed and rollback could not be "
                    "verified; inspect the retailer cart before further writes"
                ) from rollback_error
            if isinstance(original, BudgetExceeded):
                raise BudgetExceeded(
                    f"{original}; disposable cart removed"
                ) from original
            raise ProviderError(
                f"Froiz cart update failed ({type(original).__name__}); "
                "disposable cart removed"
            ) from original

        return {
            **updated,
            "retailer_cart_modified": True,
            "verified_against_reviewed_plan": True,
            "order_placed": False,
            "cart_backend": "froiz_http",
        }

    def search_products(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search the authenticated catalogue for the session's serving store.

        This is intentionally HTTP-only and read-only.  The store code is
        resolved from the selected postal code (or the session's default
        address) before the authenticated product request, so callers never
        mistake the public, non-location-aware catalogue for account prices.
        """
        effective_postal = str(postal_code or "").strip()
        if not effective_postal:
            effective_postal = str(
                self._http.default_postal_code(allow_browser_refresh=False) or ""
            ).strip()
        if not re.fullmatch(r"\d{5}", effective_postal):
            raise ProviderError(
                "Froiz authenticated search needs a five-digit postal code"
            )
        now = time.monotonic()
        with self._catalogue_store_lock:
            cached = self._catalogue_store_cache.get(effective_postal)
        store_code = cached[1] if cached and cached[0] > now else ""
        if not store_code:
            store = self._http.store_by_postal_code(
                effective_postal,
                allow_browser_refresh=False,
            )
            code = store.get("codEnt") if isinstance(store, Mapping) else None
            subcode = store.get("codSubent") if isinstance(store, Mapping) else None
            if code in (None, "") or subcode in (None, ""):
                raise ProviderError("Froiz store lookup lacked codEnt/codSubent")
            store_code = f"{code}_{subcode}"
            with self._catalogue_store_lock:
                self._catalogue_store_cache[effective_postal] = (
                    now + _CATALOGUE_STORE_TTL_SECONDS,
                    store_code,
                )
        size = max(1, min(int(limit), 100))
        return self._http.search_products(
            query,
            store=store_code,
            size=size,
            allow_browser_refresh=False,
        )

    # ------------------------------------------------- browser-backed regions

    def addresses(self) -> list[dict[str, Any]]:
        try:
            rows = self._http.addresses()
            if rows:
                return rows
        except (AuthenticationRequired, ProviderError):
            pass
        return self._browser.addresses()

    def slots(self, address_id: str | int) -> list[dict[str, Any]]:
        try:
            postal_code = self._http.postal_code_for_address(address_id)
            return self._http.delivery_calendar(postal_code)
        except (AuthenticationRequired, ProviderError):
            return self._browser.slots(address_id)

    def preview_checkout(
        self, *, expected_version: int | None, max_total: Decimal
    ) -> dict[str, Any]:
        del expected_version, max_total
        raise UnsupportedOperation(
            "Froiz has no verified non-order checkout boundary; checkout is blocked"
        )

    def create_checkout(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        del plan
        raise UnsupportedOperation(
            "Froiz checkout is blocked because orders/create places the real order"
        )

    def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        del checkout_id
        raise UnsupportedOperation(
            "Froiz checkout is unavailable because orders/create places the real order"
        )

    def set_checkout_delivery(
        self,
        checkout_id: str,
        *,
        address_id: str | int,
        slot_id: str,
        max_total: Decimal,
    ) -> dict[str, Any]:
        del checkout_id, address_id, slot_id, max_total
        raise UnsupportedOperation(
            "Froiz checkout delivery selection is unavailable because no separate "
            "pre-order checkout boundary exists"
        )

    def submit_order(
        self, checkout_id: str, *, max_total: Decimal
    ) -> dict[str, Any]:
        del checkout_id, max_total
        raise UnsupportedOperation("Froiz order submission is blocked by design")

    def close(self) -> None:
        self._http.close()
        self._browser.close()


__all__ = ["FroizAccountClient"]
