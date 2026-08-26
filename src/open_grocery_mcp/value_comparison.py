"""Offer discovery and evidence-bounded nutritional value comparisons."""

from __future__ import annotations

import html
import re
import unicodedata
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.matching import parse_quantity, select_best
from open_grocery_mcp.models import Product, as_decimal, money
from open_grocery_mcp.promotions import price_product_quantity, product_promotions
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_PER_100_RE = re.compile(r"(?:por|per)?\s*100\s*(?:g|ml)\b", re.I)
_NUTRIENT_ALIASES = {
    "protein": (r"prote[ií]nas?",),
    "fiber": (r"fibra(?:\s+alimentaria)?", r"fibre"),
    "carbohydrates": (r"hidratos?(?:\s+de\s+carbono)?", r"carbohidratos?"),
    "fat": (r"grasas?(?:\s+totales)?", r"fat"),
    "saturates": (r"(?:grasas?\s+)?saturadas?", r"saturates?"),
    "salt": (r"sal", r"salt"),
}


def _plain(value: Any) -> str:
    text = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _SPACE_RE.sub(" ", normalized).strip()


def parse_nutrient_per_100(value: Any, nutrient: str) -> Decimal | None:
    """Parse one declared nutrient only when the text states a 100 g/ml basis."""

    key = str(nutrient).strip().casefold()
    aliases = _NUTRIENT_ALIASES.get(key)
    if aliases is None:
        raise InvalidRequest(
            "nutrient must be one of: " + ", ".join(sorted(_NUTRIENT_ALIASES))
        )
    text = _plain(value)
    if not _PER_100_RE.search(text):
        return None
    for alias in aliases:
        match = re.search(
            rf"(?:{alias})\s*(?::|=|-)?\s*(\d+(?:[.,]\d+)?)\s*g\b",
            text,
            re.I,
        )
        if match:
            result = as_decimal(match.group(1))
            return result if result >= 0 else None
    return None


def comparable_measure_price(
    product: Product,
    pricing: Mapping[str, Any],
) -> Decimal | None:
    if product.price <= 0:
        return None
    effective = as_decimal(pricing.get("effective_unit_price"))
    if effective <= 0:
        return None
    if product.price_per_unit is not None and product.price_per_unit > 0:
        if product.unit not in {"kg", "L"}:
            return None
        return product.price_per_unit * effective / product.price
    parsed = parse_quantity(product.name)
    if parsed is None or parsed.dimension not in {"mass", "volume"} or parsed.value <= 0:
        return None
    # Parsed mass/volume is expressed in grams/millilitres.
    return effective * Decimal("1000") / parsed.value


def search_offer_products(
    provider: GroceryProvider,
    *,
    query: str,
    quantity: Decimal = Decimal("1"),
    limit: int = 20,
    postal_code: str | None = None,
    eco: bool = False,
    include_loyalty: bool = False,
) -> dict[str, Any]:
    term = query.strip()
    if not term:
        raise InvalidRequest("query cannot be empty")
    rows = provider.search(
        term,
        limit=max(1, min(limit, 100)),
        postal_code=postal_code,
        eco=eco,
    )
    offers: list[dict[str, Any]] = []
    for product in rows:
        if not product_promotions(product):
            continue
        pricing = price_product_quantity(
            product,
            quantity,
            include_loyalty=include_loyalty,
        )
        offers.append({"product": product.to_dict(), "pricing": pricing})
    offers.sort(
        key=lambda row: (
            -as_decimal(row["pricing"].get("savings")),
            as_decimal(row["pricing"].get("effective_total")),
        )
    )
    return {
        "store": provider.info.key,
        "query": term,
        "postal_code": postal_code,
        "quantity": float(quantity),
        "count": len(offers),
        "actionable_count": sum(
            promotion["actionable"]
            for row in offers
            for promotion in row["pricing"]["promotions"]
        ),
        "offers": offers,
        "loyalty_promotions_included": include_loyalty,
        "note": "Only explicit retailer promotion rules are actionable.",
    }


def _detailed_product(
    provider: GroceryProvider,
    product: Product,
    *,
    postal_code: str | None,
) -> Product:
    if product.nutrients:
        return product
    try:
        detail = provider.product(product.id, postal_code=postal_code)
    except Exception:
        return product
    return detail if detail.id == product.id else product


def compare_alternative_value(
    registry: ProviderRegistry,
    *,
    alternatives: Iterable[str],
    stores: Sequence[str] | None = None,
    postal_code: str | None = None,
    quantity: Decimal = Decimal("1"),
    nutrient: str | None = None,
    target_nutrient_grams: Decimal = Decimal("10"),
    search_limit: int = 10,
    eco: bool = False,
    include_loyalty: bool = False,
) -> dict[str, Any]:
    queries = [str(value).strip() for value in alternatives if str(value).strip()]
    if not queries:
        raise InvalidRequest("alternatives cannot be empty")
    if len(queries) > 20:
        raise InvalidRequest("alternatives are limited to 20 queries")
    if not quantity.is_finite() or quantity <= 0:
        raise InvalidRequest("quantity must be a positive finite number")
    if not target_nutrient_grams.is_finite() or target_nutrient_grams <= 0:
        raise InvalidRequest("target_nutrient_grams must be positive and finite")
    keys = list(stores or registry.keys())
    candidates: list[dict[str, Any]] = []
    unscored: list[dict[str, Any]] = []
    for key in keys:
        provider = registry.get(key)
        for query in queries:
            try:
                products = provider.search(
                    query,
                    limit=max(1, min(search_limit, 50)),
                    postal_code=postal_code,
                    eco=eco,
                )
                match = select_best(query, products)
            except Exception as exc:
                unscored.append(
                    {
                        "store": key,
                        "query": query,
                        "reason": f"provider search failed ({type(exc).__name__})",
                    }
                )
                continue
            if match is None:
                unscored.append({"store": key, "query": query, "reason": "no match"})
                continue
            pricing = price_product_quantity(
                match.product,
                quantity,
                include_loyalty=include_loyalty,
            )
            row: dict[str, Any] = {
                "store": key,
                "query": query,
                "product": match.product.to_dict(),
                "match_score": round(match.score, 4),
                "pricing": pricing,
            }
            measure_price = comparable_measure_price(match.product, pricing)
            if measure_price is not None:
                row["effective_price_per_kg_or_litre"] = float(measure_price)
                row["effective_price_per_kg_or_litre_text"] = money(measure_price)
            if nutrient:
                detailed = _detailed_product(
                    provider,
                    match.product,
                    postal_code=postal_code,
                )
                nutrient_value = parse_nutrient_per_100(detailed.nutrients, nutrient)
                if nutrient_value is not None and nutrient_value > 0 and measure_price is not None:
                    cost = measure_price * target_nutrient_grams / (nutrient_value * 10)
                    row["nutrition"] = {
                        "nutrient": nutrient,
                        "grams_per_100g_or_ml": float(nutrient_value),
                        "target_grams": float(target_nutrient_grams),
                        "estimated_cost": float(cost),
                        "estimated_cost_text": money(cost),
                        "basis": "retailer declaration per 100 g/ml",
                    }
                else:
                    row["nutrition_unavailable_reason"] = (
                        "verified per-100 nutrition or comparable kg/L price unavailable"
                    )
            candidates.append(row)
    price_ranking = sorted(
        candidates,
        key=lambda row: as_decimal(row["pricing"].get("effective_total")),
    )
    nutrition_ranking = sorted(
        (row for row in candidates if row.get("nutrition")),
        key=lambda row: as_decimal(row["nutrition"].get("estimated_cost")),
    )
    unit_price_ranking = sorted(
        (row for row in candidates if row.get("effective_price_per_kg_or_litre")),
        key=lambda row: as_decimal(row.get("effective_price_per_kg_or_litre")),
    )
    return {
        "alternatives": queries,
        "postal_code": postal_code,
        "quantity": float(quantity),
        "nutrient": nutrient,
        "price_ranking": price_ranking,
        "unit_price_ranking": unit_price_ranking,
        "nutrition_ranking": nutrition_ranking,
        "unscored": unscored,
        "limitations": [
            "This is a price/value comparison, not dietary or medical advice.",
            "Nutrition ranking requires retailer-declared values per 100 g/ml.",
            "Personal coupons and unobserved checkout promotions are excluded.",
        ],
    }
