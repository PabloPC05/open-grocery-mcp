#!/usr/bin/env python3
"""Live reversible verification of the Froiz HTTP cart contract.

Everything happens on a DISPOSABLE cart: create -> add -> qty 2 -> qty 1 ->
remove -> delete. The user's real channel cart is only read before and after,
to prove it was never touched. Order and payment endpoints are never called.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from open_grocery_mcp.errors import ProviderError
from open_grocery_mcp.providers.browser_normalize import is_restricted_product
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient
from open_grocery_mcp.providers.froiz_full import FroizFullProvider

MAX_ADDED_VALUE = Decimal("5.00")
ORDER_OPT_INS = (
    "OPEN_GROCERY_ENABLE_ORDER_SUBMISSION",
    "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION",
)


def enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def _as_decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal("0")
    try:
        result = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _item_product_id(item: Mapping[str, Any]) -> str:
    product = item.get("product")
    product_id = item.get("product_id")
    if not product_id and isinstance(product, Mapping):
        product_id = product.get("id")
    return str(product_id or "").strip()


def _channel_fingerprint(client: FroizHTTPClient) -> tuple[Any, ...] | None:
    cart_id = client.channel_cart_id()
    if not cart_id:
        return ("no-cart-bound",)
    raw = client.raw_cart(cart_id)
    items = sorted(
        (
            _item_product_id(i),
            str(_as_decimal(i.get("qty")).normalize()),
            str(i.get("unit") or ""),
            str(i.get("comment") or ""),
            bool(i.get("enabled", True)),
            str(
                _as_decimal(
                    (i.get("product") or {}).get("price")
                    if isinstance(i.get("product"), Mapping)
                    else None
                ).normalize()
            ),
        )
        for i in raw.get("items", []) or []
        if isinstance(i, Mapping)
    )
    total = _as_decimal(raw.get("total")).normalize()
    return (cart_id, tuple(items), str(total))


def _read_normalized_cart(client: FroizHTTPClient, cart_id: str) -> dict[str, Any]:
    """Read the retailer's state after a mutation; mutation responses are not authoritative."""

    return client.normalize_cart(client.processed_cart(cart_id))


def _cleanup_matches_probe_cart(
    payload: Mapping[str, Any], product_id: str
) -> bool:
    """Prove an initially unbound cart still contains only our known probe state."""

    items = payload.get("items")
    if not isinstance(items, list):
        return False
    if not items:
        subtotal = payload.get("subtotal")
        compared_total = subtotal if subtotal not in (None, "") else payload.get("total")
        return _as_decimal(compared_total) == 0
    if len(items) != 1 or not isinstance(items[0], Mapping):
        return False
    item = items[0]
    if (
        _item_product_id(item) != product_id
        or str(item.get("unit") or "") != "ud"
        or str(item.get("comment") or "") != ""
        or item.get("enabled") is False
    ):
        return False
    quantity = _as_decimal(item.get("qty"))
    product = item.get("product")
    if quantity not in {Decimal("1"), Decimal("2")}:
        return False
    # ``/api/cart/raw`` intentionally omits enrichment and totals.  The exact
    # product/quantity/unit/comment tuple is still authoritative for cleanup.
    if not isinstance(product, Mapping):
        return payload.get("total") in (None, "")
    price = _as_decimal(
        product.get("order_price")
        or product.get("base_price")
        or product.get("price")
    )
    if price <= 0:
        return False
    subtotal = payload.get("subtotal")
    compared_total = subtotal if subtotal not in (None, "") else payload.get("total")
    line_value_matches = (
        (price * quantity).quantize(Decimal("0.01"))
        == _as_decimal(compared_total).quantize(Decimal("0.01"))
    )
    if not line_value_matches:
        return False
    if subtotal not in (None, "") and payload.get("total") not in (None, ""):
        total = _as_decimal(payload.get("total"))
        subtotal_decimal = _as_decimal(subtotal)
        if not subtotal_decimal <= total <= subtotal_decimal + MAX_ADDED_VALUE:
            return False
    return True


def select_test_product(
    client: FroizHTTPClient,
    store: str,
    excluded_ids: set[str],
    max_added_value: Decimal,
) -> dict[str, Any]:
    unit_cap = (max_added_value / 2).quantize(Decimal("0.01"))
    for query in (
        "agua mineral 1 l",
        "leche entera 1 l",
        "arroz 1 kg",
        "sal fina 1 kg",
    ):
        for product in client.search_products(query, store=store, size=20):
            pid = str(product.get("id") or "").strip()
            name = str(product.get("name") or "").strip()
            category = str(product.get("category") or "").strip()
            price = _as_decimal(
                product.get("order_price") or product.get("base_price")
            )
            if (
                not pid
                or not name
                or pid in excluded_ids
                or product.get("enabled") is not True
                or product.get("fractional") is not False
                or product.get("per_unit") is not False
                or not (Decimal("0") < price <= unit_cap)
                or is_restricted_product(name, category)
            ):
                continue
            return {"product_id": pid, "name": name}
    raise RuntimeError("no absent, non-restricted Froiz product fits the test cap")


def verify(
    *,
    allow_reversible_cart_write: bool,
    max_added_value: Decimal = MAX_ADDED_VALUE,
    open_checkout_review: bool = False,
    review_timeout_seconds: int = 60,
    review_provider_factory: Callable[[], Any] = FroizFullProvider,
) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "froiz",
        "backend": "froiz_http",
        "retailer_write_performed": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "steps": {
            "empty_cart_created_verified": False,
            "add_verified": False,
            "quantity_two_verified": False,
            "quantity_one_verified": False,
            "remove_verified": False,
            "disposed_verified": False,
            "gone_after_delete": False,
            "addresses_read": False,
            "calendar_read": False,
        },
        "channel_cart_untouched": None,
        "checkout_review_reached": None,
        "all_non_get_blocked": None,
    }
    if not allow_reversible_cart_write:
        return 2, {
            **report,
            "reason": "explicit --allow-reversible-cart-write is required",
        }
    if not enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        return 2, {
            **report,
            "reason": "OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 is required",
        }
    if any(enabled(name) for name in ORDER_OPT_INS):
        return 2, {**report, "reason": "order-submission opt-ins must be disabled"}
    if not (Decimal("0") < max_added_value <= MAX_ADDED_VALUE):
        return 2, {**report, "reason": "max_added_value must be in (0, 5.00] EUR"}
    if open_checkout_review and not 30 <= review_timeout_seconds <= 900:
        return 2, {**report, "reason": "review timeout must be between 30 and 900 seconds"}

    client = FroizHTTPClient()
    disposable_id: str | None = None
    disposed = True
    created_from_unbound_channel = False
    write_attempts = 0
    failure_stage = "bootstrap"
    failure_type: str | None = None

    try:
        failure_stage = "delivery_reads"
        addresses = client.addresses()
        report["steps"]["addresses_read"] = True
        report["address_ids_present"] = sum(1 for a in addresses if a.get("id"))
        calendar = client.delivery_calendar()
        report["steps"]["calendar_read"] = True
        report["calendar_slots_available"] = sum(
            1 for slot in calendar if slot.get("available")
        )

        failure_stage = "channel_read"
        fingerprint_before = _channel_fingerprint(client)
        original_cart_id = (
            None
            if not fingerprint_before
            or fingerprint_before[0] == "no-cart-bound"
            else str(fingerprint_before[0])
        )
        report["started_without_channel_cart"] = original_cart_id is None
        excluded: set[str] = set()
        if original_cart_id is not None:
            excluded = {
                str(item[0])
                for item in fingerprint_before[1] or []
                if isinstance(item, tuple) and item
            }

        failure_stage = "product_selection"
        postal_code = client.default_postal_code()
        if not postal_code or len(str(postal_code)) != 5 or not str(postal_code).isdigit():
            raise RuntimeError("Froiz session has no usable default postal code")
        store = client.store_by_postal_code(str(postal_code))
        code = str(store.get("codEnt") or "").strip()
        subcode = str(store.get("codSubent") or "").strip()
        if not code or not subcode:
            raise RuntimeError("Froiz store lookup lacked codEnt/codSubent")
        product = select_test_product(client, f"{code}_{subcode}", excluded, max_added_value)

        failure_stage = "create"
        write_attempts += 1
        payload = client.create_cart([])
        disposable_id = str(payload.get("id") or "").strip() or None
        if not disposable_id:
            raise RuntimeError("Froiz did not return a disposable cart id")
        if original_cart_id is not None and disposable_id == original_cart_id:
            # Never issue a follow-up PUT/DELETE against the user's active cart.
            # In particular, do not let finally() delete it while attempting cleanup.
            disposed = True
            raise RuntimeError(
                "Froiz create returned the active channel cart id; refusing mutation"
            )
        channel_after_create = client.channel_cart_id()
        if original_cart_id is None:
            if channel_after_create not in (None, disposable_id):
                disposed = False
                raise RuntimeError(
                    "Froiz bound an unrelated cart while creating the empty probe cart"
                )
            created_from_unbound_channel = channel_after_create == disposable_id
            report["created_cart_became_active"] = created_from_unbound_channel
        elif channel_after_create == disposable_id:
            # The POST has rebound the user's active channel to the new cart.
            # Never PUT/DELETE that cart automatically: it is no longer proven
            # disposable.  The create request was deliberately empty, so this
            # fail-closed branch never introduces a product.
            disposed = True
            report["cleanup_required"] = True
            raise RuntimeError(
                "Froiz create rebound the active channel to the new cart; "
                "refusing follow-up mutation"
            )
        elif channel_after_create != original_cart_id:
            # The created id is not the active id, so finally() may safely
            # dispose this known disposable cart while refusing all PUTs.
            disposed = False
            raise RuntimeError(
                "Froiz active channel changed while creating the disposable cart"
            )
        disposed = False
        normalized = _read_normalized_cart(client, disposable_id)
        report["steps"]["empty_cart_created_verified"] = bool(
            not normalized["lines"]
            and _as_decimal(normalized.get("subtotal")) == 0
        )
        if not report["steps"]["empty_cart_created_verified"]:
            raise RuntimeError("Froiz disposable cart was not empty after create")

        failure_stage = "add"
        write_attempts += 1
        client.update_cart(
            str(disposable_id),
            [
                {
                    "product_id": product["product_id"],
                    "qty": 1,
                    "unit": "ud",
                    "comment": "",
                }
            ],
        )
        normalized = _read_normalized_cart(client, disposable_id)
        lines = {line["product_id"]: line for line in normalized["lines"]}
        report["steps"]["add_verified"] = bool(
            disposable_id
            and lines.get(product["product_id"], {}).get("quantity") == 1.0
            and Decimal("0")
            < _as_decimal(normalized.get("subtotal"))
            <= max_added_value
        )
        if not report["steps"]["add_verified"]:
            raise RuntimeError("Froiz disposable cart did not contain quantity 1 after add")

        failure_stage = "quantity_two"
        write_attempts += 1
        payload = client.update_cart(
            str(disposable_id),
            [
                {
                    "product_id": product["product_id"],
                    "qty": 2,
                    "unit": "ud",
                    "comment": "",
                }
            ],
        )
        normalized = _read_normalized_cart(client, disposable_id)
        lines = {line["product_id"]: line for line in normalized["lines"]}
        report["steps"]["quantity_two_verified"] = bool(
            lines.get(product["product_id"], {}).get("quantity") == 2.0
            and Decimal("0")
            < _as_decimal(normalized.get("subtotal"))
            <= max_added_value
        )
        if not report["steps"]["quantity_two_verified"]:
            raise RuntimeError("Froiz disposable cart did not contain quantity 2 after update")

        failure_stage = "quantity_one"
        write_attempts += 1
        payload = client.update_cart(
            str(disposable_id),
            [
                {
                    "product_id": product["product_id"],
                    "qty": 1,
                    "unit": "ud",
                    "comment": "",
                }
            ],
        )
        normalized = _read_normalized_cart(client, disposable_id)
        lines = {line["product_id"]: line for line in normalized["lines"]}
        report["steps"]["quantity_one_verified"] = bool(
            lines.get(product["product_id"], {}).get("quantity") == 1.0
            and Decimal("0")
            < _as_decimal(normalized.get("subtotal"))
            <= max_added_value
        )
        if not report["steps"]["quantity_one_verified"]:
            raise RuntimeError("Froiz disposable cart did not contain quantity 1 after update")

        if open_checkout_review:
            failure_stage = "checkout_review"
            review_provider = review_provider_factory()
            try:
                window = review_provider.open_human_review(
                    checkout_id=None,
                    checkout_review=True,
                    timeout_seconds=review_timeout_seconds,
                )
            finally:
                close_review = getattr(review_provider, "close", None)
                if callable(close_review):
                    close_review()
            report["checkout_review_reached"] = bool(window.get("window_opened"))
            report["all_non_get_blocked"] = (
                window.get("network_write_guard") == "all_non_get_blocked"
            )
            report["review_path_verified"] = (
                window.get("review_path_verified") is True
            )
            report["non_get_requests_blocked"] = int(
                window.get("non_get_requests_blocked") or 0
            )
            if not (
                report["checkout_review_reached"]
                and report["all_non_get_blocked"]
                and report["review_path_verified"]
            ):
                raise RuntimeError("Froiz checkout review was not safely reached")

        failure_stage = "remove"
        write_attempts += 1
        payload = client.update_cart(str(disposable_id), [])
        normalized = _read_normalized_cart(client, disposable_id)
        report["steps"]["remove_verified"] = bool(not normalized["lines"])
        if not report["steps"]["remove_verified"]:
            raise RuntimeError("Froiz disposable cart still contained lines after remove")

        failure_stage = "dispose"
        write_attempts += 1
        client.delete_cart(str(disposable_id))
        disposed = True
        report["steps"]["disposed_verified"] = True
        try:
            client.raw_cart(str(disposable_id))
        except ProviderError:
            report["steps"]["gone_after_delete"] = True

        failure_stage = "channel_check"
        fingerprint_after = _channel_fingerprint(client)
        report["channel_cart_untouched"] = fingerprint_after == fingerprint_before
        failure_stage = None
    except Exception as exc:
        failure_type = type(exc).__name__
    finally:
        if disposable_id and not disposed:
            cleanup_allowed = not created_from_unbound_channel
            if created_from_unbound_channel:
                try:
                    active_id = client.channel_cart_id()
                    raw_cleanup = client.raw_cart(str(disposable_id))
                    cleanup_allowed = bool(
                        active_id == disposable_id
                        and _cleanup_matches_probe_cart(
                            raw_cleanup, product["product_id"]
                        )
                    )
                    if not cleanup_allowed:
                        report["cleanup_refused_due_to_concurrent_state"] = True
                except Exception as exc:
                    report["cleanup_failure"] = type(exc).__name__
                    cleanup_allowed = False
                    del exc
            if cleanup_allowed:
                try:
                    write_attempts += 1
                    client.delete_cart(str(disposable_id))
                    disposed = True
                except Exception as exc:
                    report["cleanup_failure"] = type(exc).__name__
                    del exc
        if fingerprint_before is not None and report["channel_cart_untouched"] is None:
            try:
                report["channel_cart_untouched"] = (
                    _channel_fingerprint(client) == fingerprint_before
                )
            except Exception:
                pass
        report["write_attempts"] = write_attempts
        report["retailer_write_performed"] = write_attempts > 0
        if failure_stage:
            report["failure_stage"] = failure_stage
        if failure_type:
            report["failure_type"] = failure_type
        steps_ok = all(report["steps"].values())
        untouched = report["channel_cart_untouched"] is True
        review_ok = bool(
            not open_checkout_review
            or (
                report["checkout_review_reached"] is True
                and report["all_non_get_blocked"] is True
                and report.get("review_path_verified") is True
            )
        )
        report["ok"] = bool(
            steps_ok and untouched and review_ok and failure_stage is None
        )
        client.close()

    return (0 if report["ok"] else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reversible live Froiz cart verification on a disposable cart. "
            "Order and payment endpoints are never called."
        )
    )
    parser.add_argument(
        "--allow-reversible-cart-write",
        action="store_true",
        help="allow create/update/delete only on a disposable cart",
    )
    parser.add_argument(
        "--max-added-value",
        type=Decimal,
        default=MAX_ADDED_VALUE,
        help="maximum temporary value added to the disposable cart "
        "(hard limit: 5.00 EUR)",
    )
    parser.add_argument(
        "--open-checkout-review",
        action="store_true",
        help="open the observed checkout route with all non-GET traffic blocked",
    )
    parser.add_argument(
        "--review-timeout-seconds",
        type=int,
        default=60,
        help="visible review timeout (30-900 seconds)",
    )
    args = parser.parse_args()
    code, payload = verify(
        allow_reversible_cart_write=args.allow_reversible_cart_write,
        max_added_value=args.max_added_value,
        open_checkout_review=args.open_checkout_review,
        review_timeout_seconds=args.review_timeout_seconds,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
