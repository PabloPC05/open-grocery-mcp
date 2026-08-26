"""Semantic multi-supermarket basket optimization with delivery costs."""

from __future__ import annotations

from decimal import Decimal
from itertools import combinations, product
from math import prod
from typing import Any, Iterable, Mapping, Sequence

from open_grocery_mcp.comparison import parse_basket
from open_grocery_mcp.errors import InvalidRequest, OpenGroceryError
from open_grocery_mcp.models import as_decimal, money
from open_grocery_mcp.promotions import price_product_quantity
from open_grocery_mcp.registry import ProviderRegistry
from open_grocery_mcp.semantic_quality import assess_substitution


def _delivery(provider: Any, postal_code: str | None, subtotal: Decimal) -> dict[str, Any]:
    if not postal_code or not callable(getattr(provider, "delivery_coverage", None)):
        return {"fee": Decimal("0"), "minimum_met": None, "known": False}
    try:
        raw = provider.delivery_coverage(postal_code)
    except OpenGroceryError as exc:
        return {
            "fee": Decimal("0"),
            "minimum_met": None,
            "known": False,
            "error": type(exc).__name__,
        }
    if not isinstance(raw, Mapping):
        return {"fee": Decimal("0"), "minimum_met": None, "known": False}
    fee = as_decimal(raw.get("shipping_costs"))
    minimum = as_decimal(raw.get("minimum_order_quantity"))
    free_from = as_decimal(raw.get("minimum_shipping_free"))
    applied = Decimal("0") if free_from > 0 and subtotal >= free_from else fee
    return {
        "fee": max(Decimal("0"), applied),
        "minimum_met": minimum <= 0 or subtotal >= minimum,
        "minimum": minimum,
        "known": True,
    }


def optimize_semantic_basket(
    registry: ProviderRegistry,
    *,
    items: Iterable[str | Mapping[str, Any]],
    stores: Sequence[str] | None = None,
    postal_code: str | None = None,
    constraints: Mapping[str, Any] | None = None,
    search_limit: int = 20,
    maximum_stores: int = 4,
    review_penalty_percent: float = 5.0,
) -> dict[str, Any]:
    raw_items = list(items)
    parsed = parse_basket(raw_items)
    keys = list(stores or registry.keys())
    if not keys:
        raise InvalidRequest("at least one store is required")
    if maximum_stores < 1 or maximum_stores > 4:
        raise InvalidRequest("maximum_stores must be between 1 and 4")
    maximum_stores = min(maximum_stores, len(keys))
    if not 0 <= review_penalty_percent <= 100:
        raise InvalidRequest("review_penalty_percent must be between 0 and 100")
    if not 1 <= search_limit <= 50:
        raise InvalidRequest("search_limit must be between 1 and 50")
    if len(parsed) > 12:
        raise InvalidRequest("split optimization is limited to 12 basket lines")
    options_by_line: list[list[dict[str, Any]]] = []
    missing: list[str] = []
    for line_number, item in enumerate(parsed):
        raw_item = raw_items[line_number]
        line_settings = raw_item if isinstance(raw_item, Mapping) else {}
        line_constraints = {
            **dict(constraints or {}),
            **dict(line_settings.get("constraints") or {}),
        }
        line_intent = str(line_settings.get("intent") or "").strip() or None
        allow_review = bool(line_settings.get("allow_review_substitutes", True))
        options: list[dict[str, Any]] = []
        for key in keys:
            provider = registry.get(key)
            products = provider.search(
                item.query,
                limit=max(1, min(search_limit, 50)),
                postal_code=postal_code,
            )
            accepted: list[tuple[Decimal, dict[str, Any], Any]] = []
            for candidate in products:
                assessment = assess_substitution(
                    item.query,
                    candidate,
                    intent=line_intent,
                    constraints=line_constraints,
                )
                if assessment["verdict"] == "rejected":
                    continue
                if assessment["verdict"] == "review_substitute" and not allow_review:
                    continue
                if item.max_unit_price is not None and candidate.price > item.max_unit_price:
                    continue
                pricing = price_product_quantity(candidate, item.quantity)
                actual = as_decimal(pricing["effective_total"])
                penalty = (
                    Decimal(str(review_penalty_percent)) / Decimal("100")
                    if assessment["verdict"] == "review_substitute"
                    else Decimal("0")
                )
                accepted.append((actual * (Decimal("1") + penalty), assessment, (candidate, pricing, actual)))
            if accepted:
                objective, assessment, packed = min(accepted, key=lambda row: row[0])
                candidate, pricing, actual = packed
                options.append(
                    {
                        "store": key,
                        "request": item.to_dict(),
                        "product": candidate.to_dict(),
                        "pricing": pricing,
                        "actual": actual,
                        "objective": objective,
                        "substitution": assessment,
                        "line_constraints": line_constraints,
                        "intent": line_intent,
                    }
                )
        if not options:
            if item.required:
                missing.append(item.query)
            options.append(
                {
                    "store": None,
                    "request": item.to_dict(),
                    "actual": Decimal("0"),
                    "objective": Decimal("1000000") if item.required else Decimal("0"),
                    "reason": "no candidate satisfies semantic and explicit line constraints",
                }
            )
        options_by_line.append(options)

    combination_count = prod(len(options) for options in options_by_line)
    if combination_count > 200_000:
        assignments = []
        for count in range(1, maximum_stores + 1):
            for subset in combinations(keys, count):
                allowed = set(subset)
                selected: list[dict[str, Any]] = []
                for options in options_by_line:
                    eligible = [row for row in options if row["store"] in allowed or row["store"] is None]
                    selected.append(min(eligible, key=lambda row: row["objective"]))
                assignments.append(tuple(selected))
        exhaustive = False
    else:
        assignments = product(*options_by_line)
        exhaustive = True
    best: dict[str, Any] | None = None
    for assignment in assignments:
        used = {row["store"] for row in assignment if row["store"]}
        if len(used) > maximum_stores:
            continue
        subtotals = {
            key: sum((row["actual"] for row in assignment if row["store"] == key), Decimal("0"))
            for key in used
        }
        deliveries = {
            key: _delivery(registry.get(key), postal_code, subtotal)
            for key, subtotal in subtotals.items()
        }
        actual_total = sum(subtotals.values(), Decimal("0")) + sum(
            (row["fee"] for row in deliveries.values()), Decimal("0")
        )
        objective = sum((row["objective"] for row in assignment), Decimal("0")) + sum(
            (row["fee"] for row in deliveries.values()), Decimal("0")
        )
        minimums_met = all(
            row["minimum_met"] is not False for row in deliveries.values()
        )
        unknown_delivery_count = sum(not row["known"] for row in deliveries.values())
        rank = (not minimums_met, objective, unknown_delivery_count, actual_total, len(used))
        if best is None or rank < best["rank"]:
            best = {
                "rank": rank,
                "assignment": assignment,
                "subtotals": subtotals,
                "deliveries": deliveries,
                "actual_total": actual_total,
                "objective": objective,
                "minimums_met": minimums_met,
                "unknown_delivery_count": unknown_delivery_count,
            }
    if best is None:
        raise InvalidRequest("no basket assignment satisfies maximum_stores")
    return {
        "complete": not missing,
        "required_missing": missing,
        "stores_used": sorted({row["store"] for row in best["assignment"] if row["store"]}),
        "actual_total": float(best["actual_total"]),
        "actual_total_text": money(best["actual_total"]),
        "penalized_objective": float(best["objective"]),
        "review_penalty_percent": review_penalty_percent,
        "delivery_minimums_met": best["minimums_met"],
        "delivery_costs_complete": best["unknown_delivery_count"] == 0,
        "lines": [
            {key: value for key, value in row.items() if key not in {"actual", "objective"}}
            for row in best["assignment"]
        ],
        "store_costs": {
            key: {
                "subtotal": float(best["subtotals"][key]),
                "subtotal_text": money(best["subtotals"][key]),
                "delivery_fee": float(best["deliveries"][key]["fee"]),
                "delivery_fee_text": money(best["deliveries"][key]["fee"]),
                "minimum_met": best["deliveries"][key]["minimum_met"],
                "delivery_known": best["deliveries"][key]["known"],
                "delivery_error": best["deliveries"][key].get("error"),
            }
            for key in best["subtotals"]
        },
        "exhaustive_assignment_search": exhaustive,
        "combination_count": combination_count,
        "constraints": dict(constraints or {}),
        "note": "Actual total includes only observable promotions and public delivery fees; unknown delivery costs stay explicit and review substitutions are penalized only for ranking.",
    }


__all__ = ["optimize_semantic_basket"]
