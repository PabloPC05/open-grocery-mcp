#!/usr/bin/env python3
"""Open Gadis' reversible checkout review and restore delivery state.

The cart must already contain the owner's products.  This verifier never adds
or removes products, calls the payment-bearing checkout endpoint, submits an
order, or initiates payment.  The visible review browser blocks every non-GET
request.  Closing the window (or reaching the timeout) triggers restoration of
the original delivery context and a stable double read.
"""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from typing import Any, Callable, Mapping

from open_grocery_mcp.errors import InvalidRequest, ProviderError
from open_grocery_mcp.models import as_decimal
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


def _delivery_state(raw_cart: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(raw_cart.get(key) or "")
        for key in (
            "delivery_date",
            "schedule_range_id",
            "shipping_address_id",
            "shipping_address_owner",
            "postal_code",
            "delivery_type",
            "comments",
        )
    )


def _stable_cart(http: Any) -> dict[str, Any]:
    first = http.read_cart()
    second = http.read_cart()
    if _fingerprint(first) != _fingerprint(second):
        raise ProviderError("Gadis cart changed during checkout review")
    if _delivery_state(first) != _delivery_state(second):
        raise ProviderError("Gadis delivery context changed during checkout review")
    return second


def verify(
    *,
    max_total: Decimal,
    timeout_seconds: int = 60,
    provider_factory: Callable[[], GadisFullProvider] = GadisFullProvider,
) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "gadis",
        "checkout_review_reached": False,
        "retailer_write_performed": False,
        "state_restored": None,
        "all_non_get_blocked": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
    }
    if not enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        return 2, {**report, "reason": "retailer writes must be explicitly enabled"}
    if any(enabled(name) for name in ORDER_OPT_INS):
        return 2, {**report, "reason": "order-submission opt-ins must be disabled"}
    if not max_total.is_finite() or max_total <= 0:
        return 2, {**report, "reason": "max_total must be positive and finite"}
    if not 30 <= timeout_seconds <= 900:
        return 2, {**report, "reason": "timeout_seconds must be between 30 and 900"}

    provider = provider_factory()
    http = provider._account._http
    checkout_prepared = False
    baseline_raw: dict[str, Any] | None = None
    failure_stage = "baseline_read"
    try:
        baseline_raw = _stable_cart(http)
        failure_stage = "cart_read"
        cart = provider.real_cart()
        total = as_decimal(cart.get("total"))
        if total <= 0 or total > max_total:
            raise InvalidRequest("Gadis cart total is empty, invalid or above max_total")

        failure_stage = "delivery_read"
        addresses = [row for row in provider.delivery_addresses() if row.get("id")]
        if not addresses:
            raise InvalidRequest("Gadis account has no usable saved address")
        address = addresses[0]
        slots = [
            row
            for row in provider.delivery_slots(address["id"])
            if row.get("available") and row.get("active", True)
        ]
        if not slots:
            raise InvalidRequest("Gadis offered no available delivery slot")
        slot = slots[0]
        slot_id = str(slot.get("id") or "").strip()
        delivery_date = str(slot.get("date") or "").strip()
        if not slot_id or not delivery_date:
            raise ProviderError("Gadis slot lacked its safe id/date pair")

        failure_stage = "checkout_prepare"
        plan = provider.preview_checkout(
            expected_version=int(cart.get("version") or 0),
            max_total=max_total,
        )
        plan["delivery"] = {
            "shipping_address_id": str(address["id"]),
            "shipping_address_owner": address.get("owner"),
            "delivery_date": delivery_date,
            "schedule_range_id": slot_id,
        }
        failure_stage = "checkout_create"
        checkout = provider.create_checkout(plan)
        checkout_prepared = True
        report["retailer_write_performed"] = True
        checkout_id = str(checkout.get("checkout_id") or "").strip()
        if not checkout_id:
            raise ProviderError("Gadis checkout summary returned no local checkout id")
        failure_stage = "checkout_reread"
        reviewed = provider.get_checkout(checkout_id)
        if as_decimal(reviewed.get("total")) != total:
            raise ProviderError("Gadis checkout total changed before visible review")

        failure_stage = "visible_review"
        window = provider.open_human_review(
            checkout_id=checkout_id,
            checkout_review=True,
            timeout_seconds=timeout_seconds,
        )
        report["checkout_review_reached"] = bool(window.get("window_opened"))
        report["all_non_get_blocked"] = (
            window.get("network_write_guard") == "all_non_get_blocked"
        )
        report["review_path_verified"] = window.get("review_path_verified") is True
        report["non_get_requests_blocked"] = int(
            window.get("non_get_requests_blocked") or 0
        )
        report["review_path_present"] = bool(window.get("review_url"))
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        report["failure_stage"] = failure_stage
    finally:
        if checkout_prepared and baseline_raw is not None:
            try:
                current = _stable_cart(http)
                if _fingerprint(current) != _fingerprint(baseline_raw):
                    raise ProviderError("Gadis cart changed; automatic restoration refused")
                http.delete_schedule(str(baseline_raw.get("id") or ""))
                http.restore_cart_context(baseline_raw)
                restored = _stable_cart(http)
                report["state_restored"] = (
                    _fingerprint(restored) == _fingerprint(baseline_raw)
                    and _delivery_state(restored) == _delivery_state(baseline_raw)
                )
            except Exception as exc:
                report["state_restored"] = False
                report["restoration_failure_type"] = type(exc).__name__
        provider.close()

    report["ok"] = bool(
        report["checkout_review_reached"]
        and report["all_non_get_blocked"]
        and report.get("review_path_verified") is True
        and report["state_restored"] is True
        and not report["order_or_payment_attempted"]
    )
    return (0 if report["ok"] else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Open the safe Gadis checkout review with all non-GET traffic blocked, "
            "then restore the original delivery context."
        )
    )
    parser.add_argument("--max-total", type=Decimal, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()
    code, report = verify(
        max_total=args.max_total,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
