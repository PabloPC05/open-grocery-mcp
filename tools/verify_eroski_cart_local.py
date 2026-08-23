#!/usr/bin/env python3
"""Live reversible verification of the Eroski cart contract.

Writes run through the rendered storefront session (Tapestry zone binding),
reads run through the pure-HTTP client. The test adds one ordinary product,
verifies it appears, removes it and verifies the cart matches the initial
snapshot exactly. Order endpoints are never called.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from open_grocery_mcp.registry import ProviderRegistry

MAX_ADDED_VALUE = Decimal("5.00")
ORDER_OPT_INS = (
    "OPEN_GROCERY_ENABLE_ORDER_SUBMISSION",
    "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION",
)


def enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def _as_decimal(value):
    if value is None or isinstance(value, bool):
        return Decimal("0")
    try:
        result = Decimal(str(value).replace(",", ".").replace("€", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def verify(*, allow_reversible_cart_write: bool, max_added_value: Decimal = MAX_ADDED_VALUE):
    report = {
        "ok": False,
        "store": "eroski",
        "backend": "eroski_http_reads+ui_writes",
        "retailer_write_performed": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "steps": {
            "add_verified": False,
            "remove_verified": False,
            "state_restored": None,
        },
    }
    if not allow_reversible_cart_write:
        return 2, {**report, "reason": "explicit --allow-reversible-cart-write is required"}
    if not enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        return 2, {**report, "reason": "OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 is required"}
    if any(enabled(n) for n in ORDER_OPT_INS):
        return 2, {**report, "reason": "order-submission opt-ins must be disabled"}
    if not (Decimal("0") < max_added_value <= MAX_ADDED_VALUE):
        return 2, {**report, "reason": "max_added_value must be in (0, 5.00] EUR"}

    registry = ProviderRegistry()
    failure_stage = "bootstrap"
    failure_type = None
    try:
        provider = registry.get("eroski")

        failure_stage = "snapshot"
        initial = provider.real_cart()
        report["steps_snapshot_backend"] = initial.get("cart_backend")
        if initial.get("cart_backend") != "eroski_http":
            raise RuntimeError(
                f"cart read fell back to {initial.get('cart_backend')}"
            )
        initial_items = {
            i["product_id"]: i["quantity"] for i in initial.get("items", [])
        }
        report["initial_items"] = len(initial_items)
        initial_total = _as_decimal(initial.get("total_text"))

        failure_stage = "add"
        added = provider.add_item_via_browser("leche")
        report["retailer_write_performed"] = bool(added.get("added"))
        if not added.get("added"):
            raise RuntimeError(f"add failed: {added}")
        added_total = _as_decimal(added.get("header_total"))
        delta = added_total - initial_total
        if delta <= 0 or delta > max_added_value:
            raise RuntimeError(
                f"added value {delta} EUR outside allowed range after add"
            )

        after_add = provider.real_cart()
        if after_add.get("cart_backend") != "eroski_http":
            raise RuntimeError("post-add read lost the HTTP backend")
        after_items = {
            i["product_id"]: i["quantity"] for i in after_add.get("items", [])
        }
        new_pids = [
            pid
            for pid, qty in after_items.items()
            if qty > initial_items.get(pid, 0) or pid not in initial_items
        ]
        report["steps"]["add_verified"] = bool(new_pids)
        report["added_product_count"] = len(new_pids)

        failure_stage = "remove"
        removed_any = False
        for pid in new_pids:
            removed = provider.remove_item_via_browser(pid)
            removed_any = removed.get("removed_clicks", 0) > 0 or removed_any
        report["retailer_write_performed"] = True
        if not removed_any and new_pids:
            raise RuntimeError("no remove control was exercised")
        report["steps"]["remove_verified"] = True

        failure_stage = "restore_check"
        final = provider.real_cart()
        final_items = {
            i["product_id"]: i["quantity"] for i in final.get("items", [])
        }
        restored = (
            final_items == initial_items
            and _as_decimal(final.get("total_text")) == initial_total
        )
        report["steps"]["state_restored"] = restored
        report["final_total_text"] = final.get("total_text")
        failure_stage = None
    except Exception as exc:
        failure_type = type(exc).__name__
        report.setdefault("failure_message", str(exc)[:200])
    finally:
        try:
            registry.close()
        except Exception:
            pass
        if failure_stage:
            report["failure_stage"] = failure_stage
        if failure_type:
            report["failure_type"] = failure_type
        steps_ok = all(report["steps"].values())
        report["ok"] = bool(steps_ok and failure_stage is None)
    return (0 if report["ok"] else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reversible live Eroski cart verification. Order endpoints are "
            "never called."
        )
    )
    parser.add_argument(
        "--allow-reversible-cart-write",
        action="store_true",
        help="allow one add/remove cycle on the real basket",
    )
    parser.add_argument(
        "--max-added-value",
        type=Decimal,
        default=MAX_ADDED_VALUE,
        help="maximum temporary value added (hard limit: 5.00 EUR)",
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
