#!/usr/bin/env python3
"""Live verification of the Gadis HTTP delivery and checkout-summary contract.

Read-only by default: the delivery calendar and the cart addresses are fetched
over authenticated HTTP and only counts are reported. With explicit opt-ins it
performs one reversible schedule write (PUT then DELETE, restoring any previous
slot) only after the new state is confirmed twice and the cart fingerprint is
unchanged. It may prepare the reversible summary consumed by the GET checkout
page. It never calls ``/api/config/checkout``, submits an order, or touches
payment, Redsys or 3-D Secure endpoints.
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
        "schedule_range_id": str(raw_cart.get("schedule_range_id") or ""),
        "shipping_address_id": str(raw_cart.get("shipping_address_id") or ""),
        "shipping_address_owner": str(
            raw_cart.get("shipping_address_owner") or ""
        ),
        "postal_code": str(raw_cart.get("postal_code") or "").strip(),
        "delivery_type": str(raw_cart.get("delivery_type") or "").strip(),
        "comments": str(raw_cart.get("comments") or ""),
    }


def _stable_cart(http: Any) -> dict[str, Any]:
    """Read the cart twice and refuse cleanup if it changed between reads."""

    first = http.read_cart()
    second = http.read_cart()
    if _fingerprint(first) != _fingerprint(second) or _delivery_state(first) != _delivery_state(second):
        raise ProviderError("Gadis cart changed while verifying delivery state")
    return second


def verify(
    *,
    allow_reversible_schedule_write: bool = False,
    allow_checkout_summary: bool = False,
    allow_checkout_create: bool = False,
    provider_factory: Callable[[], GadisFullProvider] = GadisFullProvider,
) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "gadis",
        "backend": "gadis_http",
        "retailer_write_performed": False,
        "retailer_write_attempted": False,
        "cleanup_skipped": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "steps": {
            "calendar_read": False,
            "addresses_read": False,
            "schedule_applied": False,
            "schedule_removed": False,
            "checkout_summary_prepared": False,
            "checkout_created": False,
            "state_restored": None,
        },
    }
    # Backward-compatible programmatic alias. It no longer authorizes or calls
    # the payment-bearing checkout endpoint.
    allow_checkout_summary = allow_checkout_summary or allow_checkout_create
    if allow_checkout_summary and not allow_reversible_schedule_write:
        return 2, {
            **report,
            "reason": "checkout summary requires --allow-reversible-schedule-write",
        }
    if (allow_reversible_schedule_write or allow_checkout_summary) and not enabled(
        "OPEN_GROCERY_ENABLE_RETAILER_WRITES"
    ):
        return 2, {**report, "reason": "OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 is required"}
    if any(enabled(name) for name in ORDER_OPT_INS):
        return 2, {**report, "reason": "order-submission opt-ins must be disabled"}

    provider = provider_factory()
    failure_stage = "account_status"
    primary_failure_stage: str | None = None
    primary_failure_type: str | None = None
    primary_status_code: int | None = None
    primary_operation: str | None = None
    schedule_applied = False
    cleanup_attempted = False
    try:
        status = provider.account_status()
        if not status.get("authenticated") or status.get("account_backend") != "gadis_http":
            return 1, {**report, "reason": "the saved Gadis session is not authenticated"}
        http = provider._account._http

        failure_stage = "cart_read"
        cart = provider.real_cart()
        if cart.get("cart_backend") != "gadis_http" or cart.get("browser_driven") is not False:
            return 1, {**report, "reason": "the cart did not use the authenticated HTTP backend"}
        cart_id = str(cart.get("cart_id") or "").strip()
        store_id = str(cart.get("store_id") or "").strip()
        if not cart_id:
            return 1, {**report, "reason": "the cart did not expose a cart id"}

        failure_stage = "calendar_read"
        slots = http.delivery_slots(store_id=store_id or None)
        available = [s for s in slots if s.get("available") and s.get("active")]
        report["steps"]["calendar_read"] = True
        report["calendar_slots_total"] = len(slots)
        report["calendar_slots_available"] = len(available)

        failure_stage = "addresses_read"
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

        failure_stage = "baseline_read"
        baseline_raw = _stable_cart(http)
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

        if previous_delivery["had_delivery_date"] and not str(
            previous_delivery["schedule_range_id"] or ""
        ).strip():
            return 1, {
                **report,
                "reason": (
                    "the existing delivery state has no restorable slot id; "
                    "nothing was written"
                ),
            }

        # Prerequisite checks happen BEFORE any write so a missing address can
        # never leave a schedule write behind without cleanup.
        if allow_checkout_summary and not usable_addresses:
            return 1, {
                **report,
                "reason": (
                    "the account exposes no saved address id for checkout "
                    "summary preparation; nothing was written"
                ),
            }
        selected_address = usable_addresses[0] if allow_checkout_summary else None

        try:
            failure_stage = "schedule_update"
            report["retailer_write_attempted"] = True
            report["retailer_write_performed"] = True
            updated = http.update_schedule(
                cart_id,
                store_id,
                delivery_date=delivery_date,
                schedule_range_id=slot_id,
                **(
                    {
                        "shipping_address_id": str(selected_address["id"]),
                        "shipping_address_owner": selected_address.get("owner"),
                        "postal_code": str(
                            selected_address.get("postal_code") or ""
                        )
                        or None,
                    }
                    if selected_address is not None
                    else {}
                ),
            )
            failure_stage = "schedule_verify"
            applied_raw = _stable_cart(http)
            applied_delivery = _delivery_state(applied_raw)
            schedule_applied = bool(
                updated.get("cart_id") == cart_id
                and _fingerprint(applied_raw) == baseline
                and applied_delivery["delivery_date"] == delivery_date
                and str(applied_delivery["schedule_range_id"] or "") == slot_id
            )
            report["steps"]["schedule_applied"] = schedule_applied
            if not schedule_applied:
                report["cleanup_skipped"] = True
                raise ProviderError(
                    "Gadis schedule update was not confirmed; cleanup was skipped"
                )

            if allow_checkout_summary and report["steps"]["schedule_applied"]:
                failure_stage = "checkout_summary_prepare"
                address = selected_address
                assert address is not None
                summary_result = http.prepare_checkout_summary(
                    cart_id,
                    store_id,
                    shipping_address_id=str(address["id"]),
                    shipping_address_owner=address.get("owner"),
                    delivery_date=delivery_date,
                    schedule_range_id=slot_id,
                    postal_code=str(address.get("postal_code") or "") or None,
                )
                summary_raw = _stable_cart(http)
                summary_delivery = _delivery_state(summary_raw)
                summary_prepared = bool(
                    summary_result.get("summary_prepared")
                    and _fingerprint(summary_raw) == baseline
                    and str(summary_delivery["shipping_address_id"] or "")
                    == str(address["id"])
                    and summary_delivery["delivery_date"] == delivery_date
                    and str(summary_delivery["schedule_range_id"] or "") == slot_id
                )
                report["steps"]["checkout_summary_prepared"] = summary_prepared
                # Kept for stable report schemas; this verifier deliberately
                # never creates a payment-bearing checkout.
                report["steps"]["checkout_created"] = False
                if not summary_prepared:
                    raise ProviderError(
                        "Gadis checkout summary state was not confirmed"
                    )
        except Exception as exc:
            # Cleanup has its own stages, but it must never overwrite the
            # operation that actually failed. Keep only value-free metadata.
            primary_failure_stage = failure_stage
            primary_failure_type = type(exc).__name__
            status_code = getattr(exc, "status_code", None)
            primary_status_code = (
                status_code if isinstance(status_code, int) else None
            )
            operation = getattr(exc, "operation", None)
            primary_operation = operation if isinstance(operation, str) else None
            raise
        finally:
            # Never compensate a failed or ambiguous schedule write.  A
            # cleanup is allowed only after the intended new state was read
            # twice, and is attempted at most once.
            if schedule_applied and not cleanup_attempted:
                cleanup_attempted = True
                cleanup_error: str | None = None
                try:
                    failure_stage = "schedule_cleanup"
                    http.delete_schedule(cart_id)
                    http.restore_cart_context(baseline_raw)
                except Exception as exc:
                    cleanup_error = type(exc).__name__
                try:
                    failure_stage = "schedule_cleanup_verify"
                    final_raw = _stable_cart(http)
                    final_delivery = _delivery_state(final_raw)
                    expected_delivery = previous_delivery
                    report["restoration_field_matches"] = {
                        key: final_delivery.get(key) == expected_delivery.get(key)
                        for key in expected_delivery
                    }
                    restored = (
                        _fingerprint(final_raw) == baseline
                        and final_delivery == expected_delivery
                    )
                    report["steps"]["schedule_removed"] = restored
                    report["steps"]["state_restored"] = restored
                except Exception as exc:
                    cleanup_error = cleanup_error or type(exc).__name__
                    report["steps"]["state_restored"] = False
                if cleanup_error:
                    report["cleanup_failure_type"] = cleanup_error
            elif not schedule_applied:
                report["cleanup_skipped"] = True

        base_ok = bool(
            report["steps"]["calendar_read"]
            and report["steps"]["addresses_read"]
            and report["steps"]["schedule_applied"]
            and report["steps"]["schedule_removed"]
            and report["steps"]["state_restored"]
        )
        if allow_checkout_summary:
            report["ok"] = base_ok and bool(
                report["steps"]["checkout_summary_prepared"]
            )
        else:
            report["ok"] = base_ok
        return (0 if report["ok"] else 1), report
    except Exception as exc:
        failure: dict[str, Any] = {
            **report,
            "reason": "Gadis delivery verification failed",
            "failure_stage": primary_failure_stage or failure_stage,
            "failure_type": primary_failure_type or type(exc).__name__,
        }
        if primary_status_code is not None:
            failure["failure_status_code"] = primary_status_code
        if primary_operation:
            failure["failure_operation"] = primary_operation
        return 1, failure
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
        "--allow-checkout-summary",
        action="store_true",
        help=(
            "prepare and restore the reversible state used by the GET checkout "
            "page; never calls the payment-bearing checkout endpoint"
        ),
    )
    parser.add_argument(
        "--allow-checkout-create",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    code, payload = verify(
        allow_reversible_schedule_write=args.allow_reversible_schedule_write,
        allow_checkout_summary=(
            args.allow_checkout_summary or args.allow_checkout_create
        ),
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
