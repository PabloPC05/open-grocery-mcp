#!/usr/bin/env python3
"""Reach Mercadona checkout once, open a GET-only review, and restore the cart."""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from typing import Any, Callable

from open_grocery_mcp.models import as_decimal
from open_grocery_mcp.providers.mercadona_full import MercadonaFullProvider
try:
    from tools.verify_mercadona_local import (
        MAX_ADDED_VALUE,
        _cart_fingerprint,
        _cart_lines,
        _postal_code,
        _probe_product,
    )
except ModuleNotFoundError:  # Direct ``python tools/<script>.py`` execution.
    from verify_mercadona_local import (  # type: ignore[no-redef]
        MAX_ADDED_VALUE,
        _cart_fingerprint,
        _cart_lines,
        _postal_code,
        _probe_product,
    )

ORDER_OPT_INS = (
    "OPEN_GROCERY_ENABLE_ORDER_SUBMISSION",
    "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION",
)


def enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def _safe_close(provider: Any) -> None:
    try:
        provider.close()
    except Exception:
        pass


def verify(
    *,
    max_added_value: Decimal = MAX_ADDED_VALUE,
    max_total: Decimal,
    timeout_seconds: int = 60,
    provider_factory: Callable[[], MercadonaFullProvider] = MercadonaFullProvider,
) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "mercadona",
        "checkout_attempted": False,
        "checkout_created": False,
        "delivery_selected": False,
        "checkout_review_reached": False,
        "all_non_get_blocked": False,
        "probe_removed": None,
        "ambiguous_checkout_write": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
    }
    if not enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        return 2, {**report, "reason": "retailer writes must be explicitly enabled"}
    if any(enabled(name) for name in ORDER_OPT_INS):
        return 2, {**report, "reason": "order-submission opt-ins must be disabled"}
    if not max_added_value.is_finite() or not 0 < max_added_value <= MAX_ADDED_VALUE:
        return 2, {**report, "reason": "max_added_value must be in (0, 5.00]"}
    if not max_total.is_finite() or max_total < max_added_value:
        return 2, {**report, "reason": "max_total must cover max_added_value"}
    if not 30 <= timeout_seconds <= 900:
        return 2, {**report, "reason": "timeout_seconds must be between 30 and 900"}

    provider = provider_factory()
    baseline: dict[str, Any] | None = None
    probe_id: str | None = None
    probe_added = False
    failure_stage = "account_status"
    try:
        status = provider.account_status()
        if not status.get("authenticated"):
            raise RuntimeError("Mercadona session is not authenticated")

        failure_stage = "cart_baseline"
        baseline = dict(provider.real_cart())
        if _cart_lines(baseline):
            raise RuntimeError("Mercadona checkout probe requires an empty cart")
        failure_stage = "delivery_read"
        addresses = [row for row in provider.delivery_addresses() if row.get("id")]
        if not addresses:
            raise RuntimeError("Mercadona account has no usable delivery address")
        address = addresses[0]
        slots = [
            row
            for row in provider.delivery_slots(address["id"])
            if row.get("available") and row.get("open")
        ]
        if not slots:
            raise RuntimeError("Mercadona offered no available delivery slot")
        slot = slots[0]

        failure_stage = "probe_selection"
        probe = _probe_product(
            provider,
            postal_code=_postal_code([address]),
            existing_ids=set(),
            max_added_value=max_added_value,
        )
        probe_id = str(probe.id)
        failure_stage = "probe_add"
        add_plan = provider.preview_cart_update(
            [
                {
                    "product_id": probe_id,
                    "quantity": 1,
                    "name": probe.name,
                    "category": probe.category or "",
                }
            ],
            mode="merge",
            expected_version=int(baseline.get("version") or 0),
            max_total=max_added_value,
        )
        provider.commit_cart_update(add_plan)
        added = dict(provider.real_cart())
        if _cart_lines(added) != [(probe_id, Decimal("1"))]:
            raise RuntimeError("Mercadona temporary product was not confirmed")
        if not 0 < as_decimal(added.get("total")) <= max_added_value:
            raise RuntimeError("Mercadona temporary cart exceeded its cap")
        probe_added = True

        failure_stage = "checkout_create"
        checkout_plan = provider.preview_checkout(
            expected_version=int(added.get("version") or 0),
            max_total=max_total,
        )
        report["checkout_attempted"] = True
        try:
            checkout = provider.create_checkout(checkout_plan)
        except Exception:
            report["ambiguous_checkout_write"] = True
            raise
        checkout_id = str(checkout.get("checkout_id") or "").strip()
        if not checkout_id:
            raise RuntimeError("Mercadona checkout returned no id")
        report["checkout_created"] = True

        failure_stage = "delivery_select"
        selected = provider.set_checkout_delivery(
            checkout_id,
            address_id=address["id"],
            slot_id=str(slot["id"]),
            max_total=max_total,
        )
        report["delivery_selected"] = bool(
            str(selected.get("address_id") or "") == str(address["id"])
            and str(selected.get("slot_id") or "") == str(slot["id"])
        )
        if not report["delivery_selected"]:
            raise RuntimeError("Mercadona checkout did not preserve delivery")

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
    except Exception as exc:
        report["failure_stage"] = failure_stage
        report["failure_type"] = type(exc).__name__
        status_code = getattr(exc, "status_code", None)
        operation = getattr(exc, "operation", None)
        if isinstance(status_code, int):
            report["failure_status_code"] = status_code
        if isinstance(operation, str):
            report["failure_operation"] = operation
    finally:
        if probe_added and probe_id and baseline is not None:
            try:
                first = dict(provider.real_cart())
                second = dict(provider.real_cart())
                if _cart_fingerprint(first) != _cart_fingerprint(second):
                    raise RuntimeError("Mercadona cart was not stable before cleanup")
                restore_plan = provider.preview_cart_update(
                    [{"product_id": probe_id, "quantity": 0}],
                    mode="merge",
                    expected_version=int(second.get("version") or 0),
                    max_total=Decimal("0.01"),
                )
                provider.commit_cart_update(restore_plan)
                restored = dict(provider.real_cart())
                report["probe_removed"] = (
                    _cart_fingerprint(restored) == _cart_fingerprint(baseline)
                )
            except Exception as exc:
                report["probe_removed"] = False
                report["cleanup_failure_type"] = type(exc).__name__
        _safe_close(provider)

    report["ok"] = bool(
        report["checkout_created"]
        and report["delivery_selected"]
        and report["checkout_review_reached"]
        and report["all_non_get_blocked"]
        and report.get("review_path_verified") is True
        and report["probe_removed"] is True
        and not report["order_or_payment_attempted"]
    )
    return (0 if report["ok"] else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create one Mercadona checkout, open a GET-only review and remove "
            "the temporary ordinary product."
        )
    )
    parser.add_argument("--max-added-value", type=Decimal, default=MAX_ADDED_VALUE)
    parser.add_argument("--max-total", type=Decimal, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()
    code, report = verify(
        max_added_value=args.max_added_value,
        max_total=args.max_total,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
