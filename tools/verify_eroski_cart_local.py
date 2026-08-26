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


def _cart_items(cart) -> dict[str, int] | None:
    """Return a unique cart identity map; duplicate rows are not safe to probe."""

    items: dict[str, int] = {}
    for item in getattr(cart, "items", []) or []:
        product_id = str(getattr(item, "product_id", "")).strip()
        quantity = getattr(item, "quantity", 0)
        if not product_id or product_id in items:
            return None
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            return None
        items[product_id] = quantity
    return items


def _probe_cart_state_matches(
    cart,
    initial_items: dict[str, int],
    initial_total: Decimal,
    probe_pid: str,
    *,
    probe_price: Decimal,
    max_added_value: Decimal,
) -> bool:
    """Prove the cart is exactly the initial state plus one probe unit."""

    observed = _cart_items(cart)
    if observed is None:
        return False
    expected = dict(initial_items)
    expected[probe_pid] = expected.get(probe_pid, 0) + 1
    delta = _as_decimal(getattr(cart, "total_text", "")) - initial_total
    return (
        observed == expected
        and Decimal("0") < probe_price <= max_added_value
        and delta.quantize(Decimal("0.01")) == probe_price.quantize(Decimal("0.01"))
    )


def verify(
    *,
    allow_reversible_cart_write: bool,
    max_added_value: Decimal = MAX_ADDED_VALUE,
    registry: ProviderRegistry | None = None,
):
    report = {
        "ok": False,
        "store": "eroski",
        "backend": "eroski_http_reads+ui_writes",
        "retailer_write_performed": False,
        "write_attempted": False,
        "added_observed": False,
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

    registry = registry or ProviderRegistry()
    provider = None
    initial_items: dict[str, int] = {}
    initial_total = Decimal("0")
    probe_pid: str | None = None
    probe_price = Decimal("0")
    cleanup_required = False
    remove_attempted = False
    failure_stage = "bootstrap"
    failure_type = None
    try:
        provider = registry.get("eroski")

        failure_stage = "snapshot"
        initial = provider._http.read_cart()
        report["steps_snapshot_backend"] = "eroski_http"
        initial_items = _cart_items(initial)
        if initial_items is None:
            raise RuntimeError("initial Eroski cart has duplicate or invalid lines")
        report["initial_items"] = len(initial_items)
        initial_total = _as_decimal(initial.total_text)

        failure_stage = "add"
        tiles = provider._http.search_tiles("leche")
        if not tiles:
            raise RuntimeError("no search tiles rendered")
        probe_index = next(
            (
                index
                for index, tile in enumerate(tiles)
                if tile.product_ref not in initial_items
            ),
            None,
        )
        if probe_index is None:
            raise RuntimeError("no absent search tile is available for a reversible probe")
        probe_pid = tiles[probe_index].product_ref
        added = provider.add_item_via_browser(
            "leche",
            tile_index=probe_index,
            max_price=max_added_value,
            expected_product_ref=probe_pid,
        )
        write_attempted = bool(added.get("write_attempted"))
        report["write_attempted"] = write_attempted
        report["added_observed"] = bool(added.get("added"))
        # A failed/ambiguous click is still a retailer write attempt.  Arm
        # cleanup only after the UI helper says that the click was reached.
        cleanup_required = write_attempted
        report["retailer_write_performed"] = write_attempted
        # Preserve the validated card price even when the click response is
        # ambiguous; guarded cleanup can then prove the exact cart delta.
        probe_price = _as_decimal(added.get("product_price"))
        if not added.get("added"):
            raise RuntimeError(f"browser add failed: {added.get('reason', 'unknown')}")
        if not (Decimal("0") < probe_price <= max_added_value):
            raise RuntimeError("browser add did not expose a safe ordinary product price")
        after_add = provider._http.read_cart()
        added_total = _as_decimal(after_add.total_text)
        delta = added_total - initial_total
        if delta <= 0 or delta > max_added_value:
            raise RuntimeError(
                f"added value {delta} EUR outside allowed range after add"
            )

        if not _probe_cart_state_matches(
            after_add,
            initial_items,
            initial_total,
            probe_pid,
            probe_price=probe_price,
            max_added_value=max_added_value,
        ):
            raise RuntimeError("the reversible probe product was not added exactly once")
        report["steps"]["add_verified"] = True
        report["added_product_count"] = 1

        failure_stage = "remove"
        remove_attempted = True
        removed = provider.remove_item_via_browser(probe_pid, max_clicks=1)
        report["write_attempted"] = True
        removed_any = removed.get("removed_clicks", 0) > 0
        report["retailer_write_performed"] = True
        if not removed_any:
            raise RuntimeError("no remove control was exercised")
        report["steps"]["remove_verified"] = True
        cleanup_required = False

        failure_stage = "restore_check"
        final = provider._http.read_cart()
        final_items = _cart_items(final)
        restored = (
            final_items == initial_items
            and _as_decimal(final.total_text) == initial_total
        )
        report["steps"]["state_restored"] = restored
        report["final_total_text"] = final.total_text
        failure_stage = None
    except Exception as exc:
        failure_type = type(exc).__name__
    finally:
        if cleanup_required and provider is not None and probe_pid and not remove_attempted:
            report["emergency_cleanup_attempted"] = True
            try:
                observed = provider._http.read_cart()
                if not _probe_cart_state_matches(
                    observed,
                    initial_items,
                    initial_total,
                    probe_pid,
                    probe_price=probe_price,
                    max_added_value=max_added_value,
                ):
                    report["emergency_cleanup_refused"] = True
                else:
                    # A single guarded removal is allowed.  If its outcome is
                    # ambiguous, do not repeat it; leave the account manual.
                    remove_attempted = True
                    removed = provider.remove_item_via_browser(
                        probe_pid, max_clicks=1
                    )
                    recovered = provider._http.read_cart()
                    recovered_items = _cart_items(recovered)
                    report["emergency_cleanup_restored"] = (
                        removed.get("removed_clicks", 0) > 0
                        and
                        recovered_items == initial_items
                        and _as_decimal(recovered.total_text) == initial_total
                    )
            except Exception as cleanup_exc:
                report["emergency_cleanup_restored"] = False
                report["emergency_cleanup_failure_type"] = type(cleanup_exc).__name__
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
