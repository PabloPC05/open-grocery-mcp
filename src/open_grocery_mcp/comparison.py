"""Basket pricing and comparison services."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.matching import select_best
from open_grocery_mcp.models import BasketItem, money
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


def price_basket(
    provider: GroceryProvider,
    items: Sequence[BasketItem],
    *,
    postal_code: str | None = None,
    search_limit: int = 10,
    eco: bool = False,
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
        line_total = selected.product.price * item.quantity
        total += line_total
        detail = {
            "request": item.to_dict(),
            "found": True,
            **selected.to_dict(),
            "line_total": float(line_total),
            "line_total_text": money(line_total),
        }
        if selected.score < 0.55:
            detail["review_recommended"] = True
            warnings.append(
                f"Low-confidence match for {item.query!r}: {selected.product.name!r}"
            )
        details.append(detail)

    requested = len(items)
    coverage = found / requested if requested else 0.0
    return {
        "store": provider.info.key,
        "label": provider.info.label,
        "postal_code": postal_code,
        "currency": "EUR",
        "total": float(total),
        "total_text": money(total),
        "items_requested": requested,
        "items_found": found,
        "coverage": round(coverage, 4),
        "complete": required_missing == 0,
        "required_missing": required_missing,
        "details": details,
        "warnings": warnings,
        "comparison_excludes": [
            "delivery fees",
            "minimum-order rules",
            "account-specific coupons",
            "loyalty-card discounts",
            "checkout substitutions",
        ],
    }


def compare_baskets(
    registry: ProviderRegistry,
    *,
    items: Iterable[str | Mapping[str, Any]],
    stores: Sequence[str] | None = None,
    postal_code: str | None = None,
    search_limit: int = 10,
    eco: bool = False,
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
            -float(result.get("coverage", 0)),
            float(result.get("total", float("inf"))),
        )
    )
    best = next(
        (
            result["store"]
            for result in results
            if result.get("complete") and not result.get("error")
        ),
        None,
    )
    return {
        "postal_code": postal_code,
        "items": [item.to_dict() for item in parsed],
        "ranking": results,
        "best_complete_store": best,
        "note": (
            "This compares normalized product matches, not guaranteed identical SKUs. "
            "Review low-confidence matches and add delivery costs before deciding."
        ),
    }
