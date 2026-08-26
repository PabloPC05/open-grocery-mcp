#!/usr/bin/env python3
"""Safely verify the authenticated Mercadona account contract.

The command is read-only unless *both* an explicit command-line opt-in and
``OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`` are present.  The opt-in path adds
one ordinary product that is absent from the cart, verifies the resulting
cart, then restores the original cart snapshot exactly (apart from the
retailer-managed version counter).

No checkout, delivery selection, order, payment, Redsys or 3-D Secure method
is called here.  Reports are deliberately value-free: they contain counts,
booleans and exception type names, never product/address identifiers,
customer data, prices, tokens or session paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from open_grocery_mcp.models import Product
from open_grocery_mcp.providers.browser_normalize import is_restricted_product
from open_grocery_mcp.providers.mercadona_full import MercadonaFullProvider

MAX_ADDED_VALUE = Decimal("5.00")
ORDER_OPT_INS = (
    "OPEN_GROCERY_ENABLE_ORDER_SUBMISSION",
    "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION",
)


def enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def _safe_decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal("0")
    try:
        result = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _line_fields(line: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(line.get("product_id") or "").strip(),
        str(_safe_decimal(line.get("quantity")).normalize()),
        str(_safe_decimal(line.get("unit_price")).normalize()),
        str(_safe_decimal(line.get("line_total")).normalize()),
        json.dumps(line.get("sources", []), sort_keys=True, separators=(",", ":")),
    )


def _cart_fingerprint(cart: Mapping[str, Any]) -> str:
    """Hash the observable cart state without returning any cart values."""

    lines = cart.get("lines", [])
    normalized = sorted(
        _line_fields(line)
        for line in lines
        if isinstance(line, Mapping)
    ) if isinstance(lines, list) else []
    material = json.dumps(
        {
            "cart_id": str(cart.get("cart_id") or ""),
            "total": str(_safe_decimal(cart.get("total")).normalize()),
            "lines": normalized,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _cart_lines(cart: Mapping[str, Any]) -> list[tuple[str, Decimal]]:
    rows = cart.get("lines", [])
    if not isinstance(rows, list):
        return []
    return sorted(
        (
            str(line.get("product_id") or "").strip(),
            _safe_decimal(line.get("quantity")),
        )
        for line in rows
        if isinstance(line, Mapping) and str(line.get("product_id") or "").strip()
    )


def _postal_code(addresses: Sequence[Mapping[str, Any]]) -> str | None:
    for row in addresses:
        value = str(row.get("postal_code") or row.get("zip_code") or "").strip()
        if len(value) == 5 and value.isdigit():
            return value
    return None


def _probe_product(
    provider: Any,
    *,
    postal_code: str | None,
    existing_ids: set[str],
    max_added_value: Decimal,
) -> Product:
    if not postal_code:
        raise RuntimeError("a postal code is required to search a location-correct probe product")
    products = provider.search("leche", limit=25, postal_code=postal_code)
    for product in products:
        if not isinstance(product, Product):
            continue
        product_id = str(product.id).strip()
        if (
            product_id
            and product_id not in existing_ids
            and product.available
            and product.price > 0
            and product.price <= max_added_value
            and not is_restricted_product(product.name, product.category)
        ):
            return product
    raise RuntimeError("no absent ordinary probe product under the temporary value cap")


def _desired_probe_lines(
    before: Mapping[str, Any],
    product_id: str,
) -> list[tuple[str, Decimal]]:
    rows = _cart_lines(before)
    return sorted(rows + [(product_id, Decimal("1"))])


def _probe_state(
    cart: Mapping[str, Any],
    *,
    before_lines: list[tuple[str, Decimal]],
    product_id: str,
) -> str:
    observed = _cart_lines(cart)
    if observed == before_lines:
        return "unchanged"
    if observed == _desired_probe_lines({"lines": [{"product_id": p, "quantity": q} for p, q in before_lines]}, product_id):
        return "probe_present"
    return "different"


def _safe_close(provider: Any) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def verify(
    *,
    allow_reversible_cart_write: bool = False,
    max_added_value: Decimal = MAX_ADDED_VALUE,
    postal_code: str | None = None,
    provider_factory: Callable[[], MercadonaFullProvider] = MercadonaFullProvider,
) -> tuple[int, dict[str, Any]]:
    """Run the safe Mercadona checks and return ``(exit_code, value_free_report)``."""

    report: dict[str, Any] = {
        "ok": False,
        "store": "mercadona",
        "backend": "mercadona_authenticated_http",
        "retailer_write_performed": False,
        "mutation_attempted": False,
        "ambiguous_write": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "steps": {
            "session_checked": False,
            "cart_snapshot": False,
            "addresses_read": False,
            "slots_read": False,
            "add_verified": False,
            "state_restored": None,
        },
    }

    if any(enabled(name) for name in ORDER_OPT_INS):
        return 2, {
            **report,
            "reason": "order-submission opt-ins must be disabled",
        }
    try:
        max_added_value = _safe_decimal(max_added_value)
    except Exception:
        max_added_value = Decimal("0")
    if not (Decimal("0") < max_added_value <= MAX_ADDED_VALUE):
        return 2, {
            **report,
            "reason": "max_added_value must be in (0, 5.00] EUR",
        }
    if allow_reversible_cart_write and not enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        return 2, {
            **report,
            "reason": "OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 is required",
        }

    provider: Any = None
    before: dict[str, Any] | None = None
    before_fingerprint = ""
    before_lines: list[tuple[str, Decimal]] = []
    probe_id: str | None = None
    mutation_stage: str | None = None
    failure_stage = "bootstrap"
    try:
        provider = provider_factory()

        status = provider.account_status()
        if not isinstance(status, Mapping) or not status.get("authenticated"):
            raise RuntimeError("Mercadona session is not authenticated")
        report["steps"]["session_checked"] = True

        failure_stage = "cart_snapshot"
        before_value = provider.real_cart()
        if not isinstance(before_value, Mapping):
            raise RuntimeError("Mercadona cart response was not an object")
        before = dict(before_value)
        if not str(before.get("cart_id") or "").strip():
            raise RuntimeError("Mercadona cart response had no stable cart id")
        before_lines = _cart_lines(before)
        initial_total = _safe_decimal(before.get("total"))
        if before_lines and initial_total <= 0:
            raise RuntimeError(
                "Mercadona non-empty cart did not expose a positive authoritative total"
            )
        before_fingerprint = _cart_fingerprint(before)
        report["steps"]["cart_snapshot"] = True
        report["initial_cart_lines"] = len(before_lines)

        failure_stage = "addresses"
        addresses_value = provider.delivery_addresses()
        addresses = [row for row in addresses_value if isinstance(row, Mapping)] if isinstance(addresses_value, list) else []
        report["steps"]["addresses_read"] = True
        report["address_count"] = len(addresses)
        usable_addresses = [row for row in addresses if str(row.get("id") or "").strip()]
        report["usable_address_count"] = len(usable_addresses)

        failure_stage = "slots"
        if not usable_addresses:
            raise RuntimeError("Mercadona returned no usable delivery address id")
        slots_value = provider.delivery_slots(str(usable_addresses[0]["id"]))
        if not isinstance(slots_value, list):
            raise RuntimeError("Mercadona delivery slots response was not a list")
        slots = [row for row in slots_value if isinstance(row, Mapping)]
        report["steps"]["slots_read"] = True
        report["slot_count"] = len(slots)
        report["available_slot_count"] = sum(
            bool(row.get("available") and row.get("open")) for row in slots
        )

        if not allow_reversible_cart_write:
            report["ok"] = True
            failure_stage = ""
            return 0, report

        failure_stage = "probe_selection"
        existing_ids = {product_id for product_id, _ in before_lines}
        selected_address = usable_addresses[0]
        address_postal_code = _postal_code([selected_address])
        search_postal_code = address_postal_code
        if postal_code is not None:
            requested_postal_code = str(postal_code).strip()
            if (
                len(requested_postal_code) != 5
                or not requested_postal_code.isdigit()
            ):
                raise RuntimeError(
                    "Mercadona probe postal code must be five digits"
                )
            if (
                address_postal_code is None
                or requested_postal_code != address_postal_code
            ):
                raise RuntimeError(
                    "Mercadona probe postal code differs from the selected "
                    "delivery address"
                )
            search_postal_code = requested_postal_code
        probe = _probe_product(
            provider,
            # Search in the same location as the address whose slots were
            # read.  An account may contain stale/incomplete address rows;
            # using an unrelated row's postal code can select a product that
            # is not valid for the selected delivery context.
            postal_code=search_postal_code,
            existing_ids=existing_ids,
            max_added_value=max_added_value,
        )
        probe_id = str(probe.id).strip()
        expected_version = int(before.get("version") or 0)
        allowed_total = initial_total + max_added_value

        failure_stage = "add_preview"
        plan = provider.preview_cart_update(
            [
                {
                    "product_id": probe_id,
                    "quantity": 1,
                    "name": probe.name,
                    "category": probe.category or "",
                }
            ],
            mode="merge",
            expected_version=expected_version,
            max_total=allowed_total,
        )
        if not isinstance(plan, Mapping):
            raise RuntimeError("Mercadona returned an invalid cart plan")

        failure_stage = "add_commit"
        mutation_stage = "add"
        report["mutation_attempted"] = True
        added_result = provider.commit_cart_update(plan)
        report["retailer_write_performed"] = True
        if not isinstance(added_result, Mapping):
            raise RuntimeError("Mercadona add returned no cart result")
        after_add = provider.real_cart()
        if not isinstance(after_add, Mapping):
            raise RuntimeError("Mercadona cart could not be reread after add")
        added_lines = _cart_lines(after_add)
        expected_added = _desired_probe_lines(before, probe_id)
        added_total = _safe_decimal(after_add.get("total"))
        if added_lines != expected_added:
            raise RuntimeError("Mercadona cart did not contain exactly one probe product")
        if added_total <= initial_total or added_total - initial_total > max_added_value:
            raise RuntimeError("Mercadona probe total exceeded the temporary value cap")
        report["steps"]["add_verified"] = True

        failure_stage = "restore_preview"
        restored_version = int(after_add.get("version") or 0)
        restore_plan = provider.preview_cart_update(
            [{"product_id": probe_id, "quantity": 0}],
            mode="merge",
            expected_version=restored_version,
            max_total=max(initial_total, Decimal("0.01")),
        )
        if not isinstance(restore_plan, Mapping):
            raise RuntimeError("Mercadona returned an invalid restoration plan")

        failure_stage = "restore_commit"
        mutation_stage = "restore"
        report["mutation_attempted"] = True
        provider.commit_cart_update(restore_plan)
        after_restore = provider.real_cart()
        if not isinstance(after_restore, Mapping):
            raise RuntimeError("Mercadona cart could not be reread after restoration")
        restored = _cart_fingerprint(after_restore) == before_fingerprint
        report["steps"]["state_restored"] = restored
        if not restored:
            raise RuntimeError("Mercadona cart snapshot was not restored exactly")
        report["steps"]["state_restored"] = True
        failure_stage = ""
        report["ok"] = True
        return 0, report
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        if mutation_stage:
            # A failed PUT may have reached the retailer.  Read once to
            # diagnose; never retry or launch an automatic cleanup mutation.
            report["ambiguous_write"] = True
            if provider is not None and before is not None:
                try:
                    observed = provider.real_cart()
                    if isinstance(observed, Mapping):
                        observed_fp = _cart_fingerprint(observed)
                        if observed_fp == before_fingerprint:
                            report["ambiguous_write"] = False
                            report["steps"]["state_restored"] = True
                            report["write_observation"] = "snapshot_unchanged"
                        elif probe_id:
                            report["write_observation"] = _probe_state(
                                observed,
                                before_lines=before_lines,
                                product_id=probe_id,
                            )
                        else:
                            report["write_observation"] = "different"
                    else:
                        report["write_observation"] = "unreadable"
                except Exception:
                    report["write_observation"] = "unreadable"
        report["failure_stage"] = failure_stage
        return 1, report
    finally:
        if provider is not None:
            _safe_close(provider)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Mercadona authenticated cart, addresses and slots. "
            "Read-only by default; no checkout, order or payment calls."
        )
    )
    parser.add_argument(
        "--allow-reversible-cart-write",
        action="store_true",
        help="explicitly allow one ordinary-product add/remove cycle",
    )
    parser.add_argument(
        "--max-added-value",
        type=Decimal,
        default=MAX_ADDED_VALUE,
        help="maximum temporary cart value (hard limit: 5.00 EUR)",
    )
    parser.add_argument(
        "--postal-code",
        help="postal code used for the ordinary probe product search",
    )
    args = parser.parse_args()
    code, report = verify(
        allow_reversible_cart_write=args.allow_reversible_cart_write,
        max_added_value=args.max_added_value,
        postal_code=args.postal_code,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
