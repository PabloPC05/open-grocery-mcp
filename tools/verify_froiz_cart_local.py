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
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from open_grocery_mcp.errors import ProviderError
from open_grocery_mcp.providers.browser_normalize import is_restricted_product
from open_grocery_mcp.providers.froiz import FroizProvider
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient

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
        (_item_product_id(i), str(i.get("qty", "")))
        for i in raw.get("items", []) or []
        if isinstance(i, Mapping)
    )
    total = _as_decimal(raw.get("total")).normalize()
    return (cart_id, tuple(items), str(total))


def select_test_product(
    excluded_ids: set[str], max_added_value: Decimal
) -> dict[str, Any]:
    unit_cap = (max_added_value / 2).quantize(Decimal("0.01"))
    provider = FroizProvider()
    try:
        for query in (
            "agua mineral 1 l",
            "leche entera 1 l",
            "arroz 1 kg",
            "sal fina 1 kg",
        ):
            for product in provider.search(query, limit=20):
                pid = str(product.id or "").strip()
                price = _as_decimal(product.price)
                if (
                    not pid
                    or pid in excluded_ids
                    or not product.available
                    or not (Decimal("0") < price <= unit_cap)
                    or is_restricted_product(product.name, product.category or "")
                ):
                    continue
                return {"product_id": pid, "name": product.name}
    finally:
        provider.close()
    raise RuntimeError("no absent, non-restricted Froiz product fits the test cap")


def verify(
    *,
    allow_reversible_cart_write: bool,
    max_added_value: Decimal = MAX_ADDED_VALUE,
) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "froiz",
        "backend": "froiz_http",
        "retailer_write_performed": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "steps": {
            "add_verified": False,
            "quantity_two_verified": False,
            "quantity_one_verified": False,
            "remove_verified": False,
            "disposed_verified": False,
            "gone_after_delete": False,
        },
        "channel_cart_untouched": None,
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

    client = FroizHTTPClient()
    disposable_id: str | None = None
    disposed = True
    write_attempts = 0
    failure_stage = "bootstrap"
    failure_type: str | None = None

    try:
        failure_stage = "channel_read"
        fingerprint_before = _channel_fingerprint(client)
        excluded: set[str] = set()
        if fingerprint_before and fingerprint_before[0] != "no-cart-bound":
            excluded = {pid for pid, _ in fingerprint_before[1] or []}

        failure_stage = "product_selection"
        product = select_test_product(excluded, max_added_value)

        failure_stage = "create"
        write_attempts += 1
        payload = client.create_cart(
            [
                {
                    "product_id": product["product_id"],
                    "qty": 1,
                    "unit": "ud",
                    "comment": "",
                }
            ]
        )
        disposable_id = str(payload.get("id") or "").strip() or None
        disposed = False
        normalized = client.normalize_cart(payload)
        lines = {line["product_id"]: line for line in normalized["lines"]}
        report["steps"]["add_verified"] = bool(
            disposable_id
            and lines.get(product["product_id"], {}).get("quantity") == 1.0
        )

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
        normalized = client.normalize_cart(payload)
        lines = {line["product_id"]: line for line in normalized["lines"]}
        report["steps"]["quantity_two_verified"] = bool(
            lines.get(product["product_id"], {}).get("quantity") == 2.0
            and float(normalized["total"]) > 0
        )

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
        normalized = client.normalize_cart(payload)
        lines = {line["product_id"]: line for line in normalized["lines"]}
        report["steps"]["quantity_one_verified"] = bool(
            lines.get(product["product_id"], {}).get("quantity") == 1.0
        )

        failure_stage = "remove"
        write_attempts += 1
        payload = client.update_cart(str(disposable_id), [])
        normalized = client.normalize_cart(payload)
        report["steps"]["remove_verified"] = bool(not normalized["lines"])

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
            try:
                client.delete_cart(str(disposable_id))
                disposed = True
            except Exception as exc:
                report["cleanup_failure"] = type(exc).__name__
                del exc
        report["write_attempts"] = write_attempts
        report["retailer_write_performed"] = write_attempts > 0
        if failure_stage:
            report["failure_stage"] = failure_stage
        if failure_type:
            report["failure_type"] = failure_type
        steps_ok = all(report["steps"].values())
        untouched = report["channel_cart_untouched"] is True
        report["ok"] = bool(steps_ok and untouched and failure_stage is None)
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
    args = parser.parse_args()
    code, payload = verify(
        allow_reversible_cart_write=args.allow_reversible_cart_write,
        max_added_value=args.max_added_value,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
