"""Conservative evaluation of offers against cheaper similar products."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from open_grocery_mcp.equivalence import (
    assess_product_equivalence,
    assess_query_candidate,
)
from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.matching import (
    parse_quantity,
    score_product,
    tokens,
)
from open_grocery_mcp.models import Product, as_decimal, money
from open_grocery_mcp.promotions import price_product_quantity, product_promotions
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.semantic_quality import evidence_passes_budget, quality_budget
from open_grocery_mcp.value_comparison import comparable_measure_price

_PACKAGING_TOKENS = {
    "botella",
    "bote",
    "brik",
    "caja",
    "capsula",
    "capsulas",
    "dosis",
    "envase",
    "frasco",
    "garrafa",
    "gramos",
    "g",
    "kg",
    "kilo",
    "kilos",
    "lavado",
    "lavados",
    "litro",
    "litros",
    "l",
    "lata",
    "ml",
    "rollo",
    "rollos",
    "sobre",
    "sobres",
    "unidad",
    "unidades",
    "u",
    "ud",
    "uds",
}
_PRIVATE_LABELS = {
    "mercadona": {"hacendado", "deliplus", "bosque", "verde", "compy"},
    "eroski": {"eroski", "basic", "seleqtia"},
    "gadis": {"ifa", "eliges", "sabe", "gadis"},
    "froiz": {"froiz", "ifa", "eliges"},
}


def _private_label(product: Product) -> bool:
    known = _PRIVATE_LABELS.get(product.store, set())
    observed = set(tokens(" ".join((product.brand or "", product.name))))
    if product.store == "mercadona" and {"bosque", "verde"} <= observed:
        return True
    return bool(known & observed)


def _feature_tokens(product: Product, query: str) -> set[str]:
    result = set(tokens(product.name))
    result.difference_update(tokens(query))
    result.difference_update(_PACKAGING_TOKENS)
    result.difference_update({token for token in result if token.isdigit()})
    if product.brand:
        result.difference_update(tokens(product.brand))
    return result


def _similarity(
    query: str,
    left: Product,
    right: Product,
    *,
    maximum_size_ratio: float,
) -> tuple[float, dict[str, Any]]:
    semantic = assess_product_equivalence(left, right)
    if semantic["verdict"] == "different":
        return 0.0, semantic
    if semantic["uncertain_facets"]:
        return 0.0, semantic
    family = semantic["left_profile"]["family"] or semantic["right_profile"]["family"]
    if not evidence_passes_budget(semantic, family):
        return 0.0, semantic
    left_query_score, _ = score_product(query, left)
    right_query_score, _ = score_product(query, right)
    if min(left_query_score, right_query_score) < 0.4:
        return 0.0, semantic
    left_quantity = parse_quantity(left.name)
    right_quantity = parse_quantity(right.name)
    if (
        left_quantity
        and right_quantity
        and left_quantity.dimension != right_quantity.dimension
    ):
        return 0.0, semantic
    if left_quantity and right_quantity and min(
        left_quantity.value,
        right_quantity.value,
    ) > 0:
        size_ratio = float(
            max(left_quantity.value, right_quantity.value)
            / min(left_quantity.value, right_quantity.value)
        )
        if size_ratio > maximum_size_ratio:
            return 0.0, semantic
    left_features = _feature_tokens(left, query)
    right_features = _feature_tokens(right, query)
    if left_features and right_features:
        modifier_score = len(left_features & right_features) / min(
            len(left_features),
            len(right_features),
        )
    else:
        modifier_score = 0.0
    # A specific multi-token query already encodes the subtype (for example,
    # "leche entera"). A one-token query needs corroborating modifier overlap
    # so "aceite" does not compare olive oil with hair oil or sunflower oil.
    if len(set(tokens(query))) >= 2:
        return max(
            modifier_score,
            min(left_query_score, right_query_score),
            float(semantic["score"]),
        ), semantic
    return max(modifier_score, float(semantic["score"])), semantic


def _comparison_price(
    product: Product,
    pricing: Mapping[str, Any],
) -> tuple[Decimal, str] | None:
    measure = comparable_measure_price(product, pricing)
    parsed = parse_quantity(product.name)
    if measure is not None:
        if product.unit == "L" or parsed and parsed.dimension == "volume":
            return measure, "EUR/L"
        return measure, "EUR/kg"
    effective = as_decimal(pricing.get("effective_unit_price"))
    if parsed and parsed.dimension == "count" and parsed.value > 0 and effective > 0:
        return effective / parsed.value, "EUR/item"
    return None


def _promotion_quantity(product: Product, requested: Decimal) -> Decimal:
    result = requested
    for promotion in product_promotions(product):
        required = as_decimal(promotion.get("required_quantity"))
        if promotion.get("type") == "buy_x_get_y":
            required = as_decimal(promotion.get("buy_quantity")) + as_decimal(
                promotion.get("free_quantity")
            )
        if required > result:
            result = required
    return result


def evaluate_offer_value(
    provider: GroceryProvider,
    *,
    query: str,
    quantity: Decimal = Decimal("1"),
    limit: int = 50,
    postal_code: str | None = None,
    eco: bool = False,
    include_loyalty: bool = False,
    minimum_similarity: float = 0.45,
    maximum_size_ratio: float = 3.0,
    minimum_advantage_percent: Decimal = Decimal("0"),
    auto_promotion_quantity: bool = True,
) -> dict[str, Any]:
    """Classify observed offers against the cheapest comparable current item."""

    term = query.strip()
    if not term:
        raise InvalidRequest("query cannot be empty")
    if not quantity.is_finite() or quantity <= 0:
        raise InvalidRequest("quantity must be positive and finite")
    if not 0 <= minimum_similarity <= 1:
        raise InvalidRequest("minimum_similarity must be between 0 and 1")
    if not 1 <= maximum_size_ratio <= 100:
        raise InvalidRequest("maximum_size_ratio must be between 1 and 100")
    if (
        not minimum_advantage_percent.is_finite()
        or minimum_advantage_percent < 0
        or minimum_advantage_percent > 100
    ):
        raise InvalidRequest("minimum_advantage_percent must be between 0 and 100")
    observed_products = provider.search(
        term,
        limit=max(2, min(limit, 100)),
        postal_code=postal_code,
        eco=eco,
    )
    products = [
        product
        for product in observed_products
        if assess_query_candidate(term, product)["verdict"] != "different"
    ]
    offers = [product for product in products if product_promotions(product)]
    evaluated: list[dict[str, Any]] = []
    for offered in offers:
        evaluation_quantity = (
            _promotion_quantity(offered, quantity)
            if auto_promotion_quantity
            else quantity
        )
        offered_pricing = price_product_quantity(
            offered,
            evaluation_quantity,
            include_loyalty=include_loyalty,
        )
        offered_comparison = _comparison_price(offered, offered_pricing)
        row: dict[str, Any] = {
            "product": offered.to_dict(),
            "pricing": offered_pricing,
            "evaluation_quantity": float(evaluation_quantity),
            "promotion_mechanic_applied": (
                offered_pricing["applied_promotion"] is not None
            ),
        }
        if offered_comparison is None:
            row.update(
                verdict="unverified",
                reason="offer has no reliable kg/L/item comparison basis",
            )
            evaluated.append(row)
            continue
        offered_price, basis = offered_comparison
        candidates: list[
            tuple[Decimal, float, Product, dict[str, Any], dict[str, Any]]
        ] = []
        for alternative in products:
            if alternative.id == offered.id or not alternative.available:
                continue
            similarity, equivalence = _similarity(
                term,
                offered,
                alternative,
                maximum_size_ratio=maximum_size_ratio,
            )
            if similarity < minimum_similarity:
                continue
            alternative_pricing = price_product_quantity(
                alternative,
                evaluation_quantity,
                include_loyalty=include_loyalty,
            )
            alternative_comparison = _comparison_price(
                alternative,
                alternative_pricing,
            )
            if alternative_comparison is None or alternative_comparison[1] != basis:
                continue
            candidates.append(
                (
                    alternative_comparison[0],
                    similarity,
                    alternative,
                    alternative_pricing,
                    equivalence,
                )
            )
        if not candidates:
            row.update(
                verdict="unverified",
                reason="no sufficiently similar product with a comparable unit price",
                comparison_basis=basis,
                offered_comparable_price=float(offered_price),
                offered_comparable_price_text=money(offered_price),
            )
            evaluated.append(row)
            continue
        (
            alternative_price,
            similarity,
            alternative,
            alternative_pricing,
            equivalence,
        ) = min(
            candidates,
            key=lambda item: (item[0], -item[1], item[2].price),
        )
        advantage = (
            (alternative_price - offered_price) * Decimal("100") / alternative_price
        )
        required_advantage = max(Decimal("0.01"), minimum_advantage_percent)
        worthwhile = advantage >= required_advantage
        alternative_promotions = product_promotions(alternative)
        row.update(
            verdict="worthwhile" if worthwhile else "not_worthwhile",
            reason=(
                "offer is cheaper than the closest low-price alternative"
                if worthwhile
                else "a sufficiently similar product is equally priced or cheaper"
            ),
            comparison_basis=basis,
            offered_comparable_price=float(offered_price),
            offered_comparable_price_text=money(offered_price),
            cheapest_similar={
                "product": alternative.to_dict(),
                "pricing": alternative_pricing,
                "comparable_price": float(alternative_price),
                "comparable_price_text": money(alternative_price),
                "similarity": round(similarity, 4),
                "different_brand": bool(
                    offered.brand
                    and alternative.brand
                    and offered.brand.casefold() != alternative.brand.casefold()
                ),
                "private_label": _private_label(alternative),
                "also_promoted": bool(alternative_promotions),
                "equivalence": {
                    "verdict": equivalence["verdict"],
                    "score": equivalence["score"],
                    "reasons": equivalence["reasons"],
                    "uncertain_facets": equivalence["uncertain_facets"],
                    "quality_budget": quality_budget(
                        equivalence["left_profile"]["family"]
                        or equivalence["right_profile"]["family"]
                    ),
                    "passes_quality_budget": True,
                },
            },
            advantage_percent=float(advantage),
            advantage_percent_text=f"{advantage:.1f}%",
        )
        evaluated.append(row)

    order = {"worthwhile": 0, "not_worthwhile": 1, "unverified": 2}
    evaluated.sort(
        key=lambda row: (
            order[row["verdict"]],
            -as_decimal(row.get("advantage_percent")),
            row["product"]["name"],
        )
    )
    return {
        "store": provider.info.key,
        "query": term,
        "postal_code": postal_code,
        "requested_quantity": float(quantity),
        "products_observed": len(observed_products),
        "products_examined": len(products),
        "products_rejected_by_query": len(observed_products) - len(products),
        "offers_examined": len(offers),
        "maximum_size_ratio": maximum_size_ratio,
        "pricing_scenario": {
            "loyalty_requested": include_loyalty,
            "loyalty_prices_applied_only_when_observed": True,
            "personal_coupons_included": False,
            "personal_coupons_observed": sum(
                promotion.get("access_scope") == "personal_coupon"
                for product in products
                for promotion in product_promotions(product)
            ),
            "personal_coupon_policy": "reported separately and never assumed redeemable",
        },
        "counts": {
            verdict: sum(row["verdict"] == verdict for row in evaluated)
            for verdict in order
        },
        "worthwhile_offers": [
            row for row in evaluated if row["verdict"] == "worthwhile"
        ],
        "not_worthwhile_offers": [
            row for row in evaluated if row["verdict"] == "not_worthwhile"
        ],
        "unverified_offers": [
            row for row in evaluated if row["verdict"] == "unverified"
        ],
        "method": (
            "Current effective price is compared on the same kg/L/item basis "
            "against the cheapest sufficiently similar catalogue result."
        ),
    }


__all__ = ["evaluate_offer_value"]
