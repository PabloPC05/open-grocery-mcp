#!/usr/bin/env python3
"""Live, opt-in, reversible Gadis cart mutation over the authenticated HTTP flow.

This script is the local acceptance test for the Gadis HTTP cart contract. It:

* refuses to run unless ``OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`` is set;
* reads the starting cart, adds one cheap product through the full MCP workflow
  (``account_status`` -> ``get_real_cart`` -> draft -> ``prepare_real_cart_update``
  -> exact-phrase ``commit_real_cart_update``), then changes its quantity
  1 -> 2 -> 1 and finally removes it;
* re-reads the cart after every write and never retries an ambiguous response;
* caps the value added by the test at 5.00 EUR;
* asserts the final cart is identical to the starting cart.

It never opens a checkout, submits an order or initiates any payment, and it
prints no cookies, tokens, addresses, product names or other account values.
"""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from typing import Any, Mapping

from open_grocery_mcp.comparison import parse_basket, price_basket
from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.drafts import DraftCartStore
from open_grocery_mcp.errors import ConfirmationRequired
from open_grocery_mcp.providers.browser_normalize import is_restricted_product
from open_grocery_mcp.providers.gadis import GadisProvider
from open_grocery_mcp.registry import default_registry
from open_grocery_mcp.workflows import RetailerWorkflowService

ADDED_VALUE_CAP = Decimal("5.00")


def _enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def _line_signature(cart: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    lines = cart.get("lines", [])
    if not isinstance(lines, list):
        lines = []
    normalized: list[tuple[str, str]] = []
    for line in lines:
        if not isinstance(line, Mapping):
            continue
        product_id = str(line.get("product_id", "")).strip()
        quantity = Decimal(str(line.get("quantity", "0"))).normalize()
        if product_id and quantity > 0:
            normalized.append((product_id, str(quantity)))
    return tuple(sorted(normalized))


def _cheap_product() -> dict[str, Any]:
    provider = GadisProvider()
    try:
        for query in ("agua mineral 1 l", "arroz 1 kg", "leche entera 1 l"):
            for product in provider.search(query, limit=10):
                if not (0 < product.price <= ADDED_VALUE_CAP):
                    continue
                if is_restricted_product(product.name, product.category or ""):
                    continue
                return {
                    "product_id": product.id,
                    "name": product.name,
                    "price": product.price,
                    "category": product.category or "",
                }
    finally:
        provider.close()
    raise RuntimeError("no cheap, non-restricted Gadis product was found")


def verify(*, max_added_value: Decimal = ADDED_VALUE_CAP) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "gadis",
        "retailer_write_performed": False,
        "order_or_payment_attempted": False,
        "credentials_exposed": False,
    }
    if not _enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        return 2, {
            **report,
            "reason": (
                "retailer writes are disabled; set OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 "
                "to run this reversible mutation test"
            ),
        }

    registry = default_registry()
    drafts = DraftCartStore()
    confirmations = ConfirmationStore(ttl_seconds=300)
    workflows = RetailerWorkflowService(registry, drafts, confirmations)
    provider = registry.get("gadis")

    try:
        status = workflows.account_status("gadis")
        if not status.get("authenticated"):
            return 1, {
                **report,
                "reason": "the saved Gadis session is not authenticated",
                "account_backend": status.get("account_backend"),
            }

        baseline = workflows.real_cart("gadis")
        baseline_signature = _line_signature(baseline)
        baseline_total = Decimal(str(baseline.get("total", "0")))
        report["baseline_total_text"] = str(baseline.get("total_text") or "0.00")
        report["baseline_products_count"] = int(baseline.get("products_count") or 0)

        product = _cheap_product()
        product_id = product["product_id"]
        cap = baseline_total + max_added_value

        # 1) Full MCP flow: create a draft and commit the add through the
        #    prepare -> confirm -> commit workflow.
        parsed = parse_basket([{"query": product["name"], "quantity": 1}])
        basket = price_basket(provider, parsed)
        draft = drafts.create(basket)
        prepared = workflows.prepare_cart_update(
            store="gadis",
            draft_id=draft["draft_id"],
            max_total=float(cap),
            expected_cart_version=int(baseline.get("version") or 0),
            mode="merge",
        )
        confirmation_id = prepared["confirmation_id"]
        phrase = prepared["confirmation_phrase"]
        report["retailer_write_performed"] = True
        workflows.commit_cart_update(confirmation_id, phrase)

        # 2) A confirmation must be single-use.
        try:
            workflows.commit_cart_update(confirmation_id, phrase)
        except ConfirmationRequired:
            report["confirmation_single_use"] = True
        else:
            report["confirmation_single_use"] = False
            return 1, {
                **report,
                "reason": "a consumed confirmation was accepted a second time",
            }

        # 3) Read back after the write and verify quantity 1.
        after_add = workflows.real_cart("gadis")
        if not _has_quantity(after_add, product_id, "1"):
            return 1, {**report, "reason": "cart did not reach quantity 1 after add"}

        # 4) 1 -> 2 -> 1 using the provider's prepare/commit (whole-unit).
        for target in ("2", "1"):
            current = workflows.real_cart("gadis")
            plan = provider.preview_cart_update(
                [
                    {
                        "product_id": product_id,
                        "name": product["name"],
                        "quantity": int(target),
                        "unit_price": float(product["price"]),
                    }
                ],
                mode="merge",
                expected_version=int(current.get("version") or 0),
                max_total=cap,
            )
            provider.commit_cart_update(plan)
            after = workflows.real_cart("gadis")
            if not _has_quantity(after, product_id, target):
                return 1, {**report, "reason": f"cart did not reach quantity {target}"}

        # 5) Remove the added product.
        current = workflows.real_cart("gadis")
        plan = provider.preview_cart_update(
            [
                {
                    "product_id": product_id,
                    "name": product["name"],
                    "quantity": 0,
                    "unit_price": float(product["price"]),
                }
            ],
            mode="merge",
            expected_version=int(current.get("version") or 0),
            max_total=cap,
        )
        provider.commit_cart_update(plan)

        final = workflows.real_cart("gadis")
        report["final_products_count"] = int(final.get("products_count") or 0)
        report["cart_restored"] = _line_signature(final) == baseline_signature
        if not report["cart_restored"]:
            return 1, {
                **report,
                "reason": "the cart was not restored to its starting lines",
            }

        report.update(
            {
                "ok": True,
                "cart_backend": final.get("cart_backend"),
                "added_value_cap_text": "5.00",
                "added_product_unit_price": str(product["price"]),
                "writes": 5,
                "reads_after_each_write": True,
            }
        )
        return 0, report
    finally:
        registry.close()


def _has_quantity(cart: Mapping[str, Any], product_id: str, quantity: str) -> bool:
    lines = cart.get("lines", [])
    if not isinstance(lines, list):
        return False
    for line in lines:
        if not isinstance(line, Mapping):
            continue
        if str(line.get("product_id", "")) == product_id:
            return str(Decimal(str(line.get("quantity", "0"))).normalize()) == quantity
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reversible live Gadis cart mutation over the authenticated HTTP flow."
    )
    parser.add_argument("--max-added-value", type=Decimal, default=ADDED_VALUE_CAP)
    args = parser.parse_args()
    code, payload = verify(max_added_value=args.max_added_value)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
