"""Basket pricing and comparison services."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.matching import select_best
from open_grocery_mcp.models import BasketItem, as_decimal, money
from open_grocery_mcp.promotions import price_product_quantity
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry


def parse_basket(items: Iterable[str | Mapping[str, Any]]) -> list[BasketItem]:
    try:
        parsed = [BasketItem.from_value(item) for item in items]
    except (TypeError, ValueError) as exc:
        raise InvalidRequest(str(exc)) from exc
    if not parsed:
        raise InvalidRequest("basket must contain at least one item")
    if len(parsed) > 100:
        raise InvalidRequest("basket is limited to 100 lines per comparison")
    return parsed


def _delivery_estimate(
    provider: GroceryProvider,
    *,
    postal_code: str | None,
    subtotal: Decimal,
) -> dict[str, Any] | None:
    """Read an optional public delivery/minimum-order policy from a provider."""

    if not postal_code:
        return None
    coverage = getattr(provider, "delivery_coverage", None)
    if not callable(coverage):
        return None
    raw = coverage(postal_code)
    if not isinstance(raw, Mapping):
        return None
    listed_fee = as_decimal(raw.get("shipping_costs"))
    minimum_order = as_decimal(raw.get("minimum_order_quantity"))
    free_from = as_decimal(raw.get("minimum_shipping_free"))
    applied_fee = (
        Decimal("0")
        if free_from > 0 and subtotal >= free_from
        else max(Decimal("0"), listed_fee)
    )
    minimum_met = minimum_order <= 0 or subtotal >= minimum_order
    estimated_total = subtotal + applied_fee
    return {
        "store_id": raw.get("store_id"),
        "postal_code": postal_code,
        "listed_delivery_fee": float(listed_fee),
        "listed_delivery_fee_text": money(listed_fee),
        "applied_delivery_fee": float(applied_fee),
        "applied_delivery_fee_text": money(applied_fee),
        "minimum_order": float(minimum_order),
        "minimum_order_text": money(minimum_order),
        "free_delivery_from": float(free_from),
        "free_delivery_from_text": money(free_from),
        "minimum_order_met": minimum_met,
        "estimated_checkout_total": float(estimated_total),
        "estimated_checkout_total_text": money(estimated_total),
        "source": "public retailer delivery policy",
    }


def price_basket(
    provider: GroceryProvider,
    items: Sequence[BasketItem],
    *,
    postal_code: str | None = None,
    search_limit: int = 10,
    eco: bool = False,
    include_loyalty: bool = False,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    total = Decimal("0")
    found = 0
    required_missing = 0
    warnings: list[str] = []

    for item in items:
        products = provider.search(
            item.query,
            limit=max(1, min(search_limit, 50)),
            postal_code=postal_code,
            eco=eco,
        )
        selected = select_best(
            item.query,
            products,
            max_unit_price=item.max_unit_price,
        )
        if selected is None:
            if item.required:
                required_missing += 1
            details.append(
                {
                    "request": item.to_dict(),
                    "found": False,
                    "reason": (
                        "no sufficiently similar product within constraints"
                        if products
                        else "store returned no search results"
                    ),
                }
            )
            continue

        found += 1
        promotion_pricing = price_product_quantity(
            selected.product,
            item.quantity,
            include_loyalty=include_loyalty,
        )
        line_total = as_decimal(promotion_pricing["effective_total"])
        total += line_total
        detail = {
            "request": item.to_dict(),
            "found": True,
            **selected.to_dict(),
            "line_total": float(line_total),
            "line_total_text": money(line_total),
            "promotion_pricing": promotion_pricing,
        }
        if promotion_pricing["applied_promotion"] is not None:
            detail["promotion_applied"] = True
        for warning in promotion_pricing["warnings"]:
            warnings.append(f"{selected.product.name}: {warning}")
        if selected.score < 0.55:
            detail["review_recommended"] = True
            warnings.append(
                f"Low-confidence match for {item.query!r}: {selected.product.name!r}"
            )
        details.append(detail)

    requested = len(items)
    coverage = found / requested if requested else 0.0
    complete = required_missing == 0
    delivery = _delivery_estimate(
        provider,
        postal_code=postal_code,
        subtotal=total,
    )
    checkout_eligible: bool | None = None
    estimated_checkout_total: float | None = None
    estimated_checkout_total_text: str | None = None
    exclusions = [
        "account-specific coupons",
        "checkout substitutions",
    ]
    exclusions.insert(
        1,
        (
            "unverified loyalty eligibility"
            if include_loyalty
            else "loyalty-card discounts"
        ),
    )
    if delivery is None:
        exclusions[0:0] = ["delivery fees", "minimum-order rules"]
    else:
        checkout_eligible = complete and bool(delivery["minimum_order_met"])
        estimated_checkout_total = float(delivery["estimated_checkout_total"])
        estimated_checkout_total_text = str(delivery["estimated_checkout_total_text"])
        if not delivery["minimum_order_met"]:
            warnings.append(
                f"Basket subtotal {money(total)} EUR is below the retailer minimum "
                f"{delivery['minimum_order_text']} EUR"
            )

    result: dict[str, Any] = {
        "store": provider.info.key,
        "label": provider.info.label,
        "postal_code": postal_code,
        "currency": "EUR",
        # ``total`` remains the product subtotal for backward compatibility.
        "total": float(total),
        "total_text": money(total),
        "subtotal": float(total),
        "subtotal_text": money(total),
        "items_requested": requested,
        "items_found": found,
        "coverage": round(coverage, 4),
        "complete": complete,
        "required_missing": required_missing,
        "checkout_eligible": checkout_eligible,
        "details": details,
        "warnings": warnings,
        "comparison_excludes": exclusions,
        "loyalty_promotions_included": include_loyalty,
    }
    if delivery is not None:
        result["delivery"] = delivery
        result["estimated_checkout_total"] = estimated_checkout_total
        result["estimated_checkout_total_text"] = estimated_checkout_total_text
    return result


def _ranking_total(result: Mapping[str, Any]) -> float:
    value = result.get("estimated_checkout_total")
    if value is None:
        value = result.get("total", float("inf"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def compare_baskets(
    registry: ProviderRegistry,
    *,
    items: Iterable[str | Mapping[str, Any]],
    stores: Sequence[str] | None = None,
    postal_code: str | None = None,
    search_limit: int = 10,
    eco: bool = False,
    include_loyalty: bool = False,
) -> dict[str, Any]:
    parsed = parse_basket(items)
    keys = list(stores or registry.keys())
    if not keys:
        raise InvalidRequest("at least one store is required")
    if len(keys) > 20:
        raise InvalidRequest("a single comparison is limited to 20 stores")

    results: list[dict[str, Any]] = []
    workers = min(8, len(keys))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="grocery-compare") as pool:
        future_to_key = {
            pool.submit(
                price_basket,
                registry.get(key),
                parsed,
                postal_code=postal_code,
                search_limit=search_limit,
                eco=eco,
                include_loyalty=include_loyalty,
            ): key
            for key in keys
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results.append(future.result())
            except Exception as exc:  # Keep one failing store from hiding the rest.
                results.append(
                    {
                        "store": key,
                        "complete": False,
                        "checkout_eligible": False,
                        "items_requested": len(parsed),
                        "items_found": 0,
                        "coverage": 0.0,
                        "error": str(exc),
                    }
                )

    results.sort(
        key=lambda result: (
            bool(result.get("error")),
            not bool(result.get("complete")),
            result.get("checkout_eligible") is False,
            -float(result.get("coverage", 0)),
            _ranking_total(result),
        )
    )
    best_complete = next(
        (
            result["store"]
            for result in results
            if result.get("complete") and not result.get("error")
        ),
        None,
    )
    best_checkout = next(
        (
            result["store"]
            for result in results
            if result.get("complete")
            and result.get("checkout_eligible") is not False
            and not result.get("error")
        ),
        None,
    )
    return {
        "postal_code": postal_code,
        "items": [item.to_dict() for item in parsed],
        "ranking": results,
        "best_complete_store": best_complete,
        "best_estimated_checkout_store": best_checkout,
        "loyalty_promotions_included": include_loyalty,
        "note": (
            "This compares normalized product matches, not guaranteed identical SKUs. "
            "Where a provider exposes a public delivery policy, ranking uses its "
            "estimated checkout total; otherwise delivery remains excluded."
        ),
    }
