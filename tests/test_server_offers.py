from __future__ import annotations

from decimal import Decimal

import pytest

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry
from open_grocery_mcp import server


class OfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search", "compare"),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        return [
            Product(
                store="alpha",
                id="1",
                name=f"{query} 200 g",
                price=Decimal("2"),
                price_per_unit=Decimal("10"),
                unit="kg",
                nutrients="Por 100 g: proteínas 20 g",
                metadata={
                    "promotion": {
                        "current_price": 2,
                        "previous_price": 3,
                    }
                },
            )
        ]

    def product(self, product_id: str, **_: object) -> Product:
        return self.search("tofu")[0]


@pytest.fixture
def offer_registry(monkeypatch: pytest.MonkeyPatch) -> ProviderRegistry:
    provider = OfferProvider()
    registry = ProviderRegistry(factories={"alpha": lambda: provider})
    monkeypatch.setattr(server, "_registry", registry)
    return registry


def test_search_offers_mcp_tool_prices_requested_quantity(offer_registry) -> None:
    del offer_registry

    result = server.search_offers("alpha", "tofu", quantity=2)

    assert result["count"] == 1
    assert result["actionable_count"] == 1
    assert result["offers"][0]["pricing"]["effective_total_text"] == "4.00"


def test_compare_alternatives_mcp_tool_and_numeric_validation(offer_registry) -> None:
    del offer_registry

    result = server.compare_alternatives(
        ["tofu"],
        stores=["alpha"],
        nutrient="protein",
    )

    assert result["nutrition_ranking"][0]["nutrition"]["estimated_cost_text"] == "0.50"
    with pytest.raises(InvalidRequest, match="positive finite"):
        server.search_offers("alpha", "tofu", quantity=float("nan"))


def test_filter_worthwhile_offers_is_exposed_and_rejects_nan(offer_registry) -> None:
    del offer_registry

    result = server.filter_worthwhile_offers("alpha", "tofu")

    assert result["counts"]["unverified"] == 1
    with pytest.raises(InvalidRequest, match="between 0 and 100"):
        server.filter_worthwhile_offers(
            "alpha",
            "tofu",
            minimum_advantage_percent=float("nan"),
        )
