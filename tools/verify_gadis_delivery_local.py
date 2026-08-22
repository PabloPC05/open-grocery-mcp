#!/usr/bin/env python3
"""Live verification of the Gadis HTTP delivery and checkout contract.

Read-only by default: the delivery calendar and the cart addresses are fetched
over authenticated HTTP and only counts are reported. With explicit opt-ins it
performs one reversible schedule write (PUT then DELETE, restoring any previous
slot) and may create a checkout exactly once. It never submits an order and
never touches payment, Redsys or 3-D Secure endpoints.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Callable, Mapping

from open_grocery_mcp.errors import AuthenticationRequired, ProviderError
from open_grocery_mcp.providers.gadis_full import GadisFullProvider

ORDER_OPT_INS = (
    "OPEN_GROCERY_ENABLE_ORDER_SUBMISSION",
    "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION",
)


def enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def _fingerprint(raw_cart: Mapping[str, Any]) -> tuple[Any, ...]:
    products = raw_cart.get("products", [])
    lines = []
    for row in products if isinstance(products, list) else []:
        if isinstance(row, Mapping):
            lines.append(
                (
                    str(row.get("product_id", "")).strip(),
                    str(row.get("amount", "")),
                )
            )
    return (
        tuple(sorted(lines)),
        str(raw_cart.get("total_cart_price", "")),
        str(raw_cart.get("total_products", "")),
        str(raw_cart.get("id", "")).strip(),
    )


def _delivery_state(raw_cart: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "had_delivery_date": bool(str(raw_cart.get("delivery_date") or "").strip()),
        "delivery_date": str(raw_cart.get("delivery_date") or "").strip() or None,
        "schedule_range_id": raw_cart.get("schedule_range_id"),
    }


def verify(
    *,
    allow_reversible_schedule_write: bool = False,
    allow_checkout_create: bool = False,
    provider_factory: Callable[[], GadisFullProvider] = GadisFullProvider,
) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "gadis",
        "backend": "gadis_http",
        "retailer_write_performed": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "steps": {
            "calendar_read": False,
            "addresses_read": False,
            "schedule_applied": False,
            "schedule_removed": False,
            "checkout_created": False,
            "state_restored": None,
        },
    }
    if allow_checkout_create and not allow_reversible_schedule_write:
        return 2, {
            **report,
            "reason": "--allow-checkout-create requires --allow-reversible-schedule-write",
        }
    if (allow_reversible_schedule_write or allow_checkout_create) and not enabled(
        "OPEN_GROCERY_ENABLE_RETAILER_WRITES"
    ):
        return 2, {**report, "reason": "OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 is required"}
    if any(enabled(name) for name in ORDER_OPT_INS):
        return 2, {**report, "reason": "order-submission opt-ins must be disabled"}

    provider = provider_factory()
    try:
        status = provider.account_status()
        if not status.get("authenticated") or status.get("account_backend") != "gadis_http":
            return 1, {**report, "reason": "the saved Gadis session is not authenticated"}
        http = provider._account._http

        cart = provider.real_cart()
        if cart.get("cart_backend") != "gadis_http" or cart.get("browser_driven") is not False:
            return 1, {**report, "reason": "the cart did not use the authenticated HTTP backend"}
        cart_id = str(cart.get("cart_id") or "").strip()
        store_id = str(cart.get("store_id") or "").strip()
        if not cart_id:
            return 1, {**report, "reason": "the cart did not expose a cart id"}

        slots = http.delivery_slots(store_id=store_id or None)
        available = [s for s in slots if s.get("available") and s.get("active")]
        report["steps"]["calendar_read"] = True
        report["calendar_slots_total"] = len(slots)
        report["calendar_slots_available"] = len(available)

        addresses = http.addresses(cart_id)
        usable_addresses = [a for a in addresses if a.get("id")]
        client_rows: list[dict[str, Any]] = []
        if not usable_addresses:
            try:
                client_rows = [
                    a for a in http.client_addresses() if a.get("id")
                ]
            except (AuthenticationRequired, ProviderError):
                client_rows = []
        report["steps"]["addresses_read"] = True
        report["cart_address_rows"] = len(addresses)
        report["client_address_ids_present"] = len(client_rows)
        usable_addresses = usable_addresses or client_rows
        report["usable_address_ids"] = len(usable_addresses)

        baseline_raw = http.read_cart()
        baseline = _fingerprint(baseline_raw)
        previous_delivery = _delivery_state(baseline_raw)

        if not allow_reversible_schedule_write:
            report["ok"] = bool(
                report["steps"]["calendar_read"] and report["steps"]["addresses_read"]
            )
            return (0 if report["ok"] else 1), report

        if not available:
            return 1, {**report, "reason": "no available delivery slot was offered"}
        slot = available[0]
        slot_id = str(slot.get("id") or "").strip()
        delivery_date = str(slot.get("date") or "").strip()
        if not slot_id or not delivery_date:
            return 1, {**report, "reason": "the offered slot lacks id or date"}

        # Prerequisite checks happen BEFORE any write so a missing address can
        # never leave a schedule write behind without cleanup.
        if allow_checkout_create and not usable_addresses:
            return 1, {
                **report,
                "reason": (
                    "the account exposes no saved address id for checkout "
                    "creation; nothing was written"
                ),
            }

        try:
            updated = http.update_schedule(
                cart_id,
                store_id,
                delivery_date=delivery_date,
                schedule_range_id=slot_id,
            )
            report["retailer_write_performed"] = True
            applied_raw = http.read_cart()
            applied_delivery = _delivery_state(applied_raw)
            report["steps"]["schedule_applied"] = bool(
                updated.get("cart_id") == cart_id
                and applied_delivery["delivery_date"] == delivery_date
                and str(applied_delivery["schedule_range_id"] or "") == slot_id
            )

            if allow_checkout_create and report["steps"]["schedule_applied"]:
                address = usable_addresses[0]
                checkout_result = http.create_checkout(
                    cart_id,
                    store_id,
                    shipping_address_id=str(address["id"]),
                    shipping_address_owner=address.get("owner"),
                    delivery_date=delivery_date,
                    schedule_range_id=slot_id,
                )
                report["steps"]["checkout_created"] = bool(
                    checkout_result.get("checkout_present")
                )
                report["checkout_removed_products"] = len(
                    checkout_result.get("removed_products") or []
                )
                report["checkout_price_changes"] = bool(
                    checkout_result.get("has_product_price_changes")
                )
                report["checkout_order_placed"] = bool(
                    checkout_result.get("order_placed")
                )
        finally:
            # Cleanup runs no matter what happened above.
            cleanup_error: str | None = None
            try:
                http.delete_schedule(cart_id)
                if previous_delivery["had_delivery_date"] and previous_delivery[
                    "delivery_date"
                ]:
                    http.update_schedule(
                        cart_id,
                        store_id,
                        delivery_date=str(previous_delivery["delivery_date"]),
                        schedule_range_id=previous_delivery["schedule_range_id"],
                    )
            except Exception as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
            try:
                final_raw = http.read_cart()
                final_delivery = _delivery_state(final_raw)
                report["steps"]["schedule_removed"] = not (
                    final_delivery["delivery_date"]
                    and not previous_delivery["had_delivery_date"]
                )
                report["steps"]["state_restored"] = (
                    _fingerprint(final_raw) == baseline
                    and final_delivery["delivery_date"]
                    == previous_delivery["delivery_date"]
                )
            except Exception as exc:
                cleanup_error = cleanup_error or f"{type(exc).__name__}: {exc}"
                report["steps"]["state_restored"] = False
            if cleanup_error:
                report["cleanup_failure"] = cleanup_error

        base_ok = bool(
            report["steps"]["calendar_read"]
            and report["steps"]["addresses_read"]
            and report["steps"]["schedule_applied"]
            and report["steps"]["schedule_removed"]
            and report["steps"]["state_restored"]
        )
        if allow_checkout_create:
            report["ok"] = base_ok and bool(
                report["steps"]["checkout_created"]
                and report.get("checkout_order_placed") is False
            )
        else:
            report["ok"] = base_ok
        return (0 if report["ok"] else 1), report
    except Exception as exc:
        return 1, {
            **report,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    finally:
        provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the live Gadis HTTP delivery contract. Order and payment "
            "endpoints are never called."
        )
    )
    parser.add_argument(
        "--allow-reversible-schedule-write",
        action="store_true",
        help="allow attaching and removing one delivery slot on the real cart",
    )
    parser.add_argument(
        "--allow-checkout-create",
        action="store_true",
        help=(
            "additionally create a checkout once over HTTP; still never places "
            "an order or initiates payment"
        ),
    )
    args = parser.parse_args()
    code, payload = verify(
        allow_reversible_schedule_write=args.allow_reversible_schedule_write,
        allow_checkout_create=args.allow_checkout_create,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
