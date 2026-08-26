from __future__ import annotations

from decimal import Decimal

import pytest

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry
from open_grocery_mcp.value_comparison import (
    compare_alternative_value,
    parse_nutrient_per_100,
    search_offer_products,
)


class StaticProvider(GroceryProvider):
    def __init__(self, key: str, products: dict[str, list[Product]]) -> None:
        self.info = StoreInfo(
            key=key,
            label=key.title(),
            country="ES",
            languages=("es",),
            capabilities=("search", "compare"),
        )
        self.products = products

    def search(self, query: str, **_: object) -> list[Product]:
        return self.products.get(query, [])

    def product(self, product_id: str, **_: object) -> Product:
        return next(
            item
            for products in self.products.values()
            for item in products
            if item.id == product_id
        )


def p(
    key: str,
    product_id: str,
    name: str,
    price: str,
    nutrients: str | None,
    metadata: dict[str, object] | None = None,
) -> Product:
    return Product(
        store=key,
        id=product_id,
        name=name,
        price=Decimal(price),
        price_per_unit=Decimal(price) * 5,
        unit="kg",
        nutrients=nutrients,
        metadata=metadata or {},
    )


def test_parse_nutrient_requires_explicit_per_100_basis() -> None:
    assert parse_nutrient_per_100("Por 100 g: proteínas 12,5 g", "protein") == Decimal("12.5")
    assert parse_nutrient_per_100("Proteínas: 12,5 g por ración", "protein") is None
    with pytest.raises(InvalidRequest, match="nutrient must be"):
        parse_nutrient_per_100("Por 100 g: calcio 2 g", "calcium")


def test_offer_search_returns_only_observed_promotions() -> None:
    discounted = p(
        "alpha",
        "1",
        "Tofu 200 g",
        "2.00",
        None,
        {
            "promotion": {
                "current_price": 2,
                "previous_price": 2.5,
            }
        },
    )
    ordinary = p("alpha", "2", "Tofu natural 200 g", "1.80", None)
    provider = StaticProvider("alpha", {"tofu": [discounted, ordinary]})

    result = search_offer_products(provider, query="tofu", quantity=Decimal("2"))

    assert result["count"] == 1
    assert result["offers"][0]["product"]["id"] == "1"
    assert result["offers"][0]["pricing"]["savings_text"] == "1.00"


def test_alternatives_rank_price_and_verified_nutrient_value_separately() -> None:
    tofu = p(
        "alpha",
        "1",
        "Tofu natural 200 g",
        "2.00",
        "Valores por 100 g: proteínas 20 g; grasas 8 g",
    )
    lentils = p(
        "alpha",
        "2",
        "Lentejas cocidas 200 g",
        "1.00",
        "Por 100 g proteínas: 5 g",
    )
    provider = StaticProvider("alpha", {"tofu": [tofu], "lentejas": [lentils]})
    registry = ProviderRegistry(factories={"alpha": lambda: provider})

    result = compare_alternative_value(
        registry,
        alternatives=["tofu", "lentejas"],
        nutrient="protein",
        target_nutrient_grams=Decimal("10"),
    )

    assert result["price_ranking"][0]["query"] == "lentejas"
    assert result["nutrition_ranking"][0]["query"] == "tofu"
    assert result["nutrition_ranking"][0]["nutrition"]["estimated_cost_text"] == "0.50"


def test_alternatives_do_not_invent_missing_nutrition() -> None:
    item = p("alpha", "1", "Tofu 200 g", "2.00", "Proteínas 20 g por ración")
    provider = StaticProvider("alpha", {"tofu": [item]})
    registry = ProviderRegistry(factories={"alpha": lambda: provider})

    result = compare_alternative_value(
        registry,
        alternatives=["tofu"],
        nutrient="protein",
    )

    assert result["nutrition_ranking"] == []
    assert "nutrition_unavailable_reason" in result["price_ranking"][0]
