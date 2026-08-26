from __future__ import annotations

from decimal import Decimal

from open_grocery_mcp.basket_optimization import optimize_semantic_basket
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry


class BasketProvider(GroceryProvider):
    def __init__(self, key: str, prices: dict[str, str], delivery: str) -> None:
        self.info = StoreInfo(key, key.title(), "ES", ("es",), ("search", "coverage"))
        self.prices = prices
        self.delivery = delivery

    def search(self, query: str, **_: object) -> list[Product]:
        price = self.prices.get(query)
        if price is None:
            return []
        return [
            Product(
                self.info.key,
                query,
                query,
                Decimal(price),
                ingredients="trigo",
            )
        ]

    def delivery_coverage(self, postal_code: str) -> dict[str, object]:
        assert postal_code == "15001"
        return {
            "shipping_costs": self.delivery,
            "minimum_order_quantity": 0,
            "minimum_shipping_free": 0,
        }


def test_optimizer_accounts_for_delivery_and_line_price_constraints() -> None:
    cheap = BasketProvider("cheap", {"harina de trigo": "1", "sal fina": "1"}, "8")
    delivered = BasketProvider("delivered", {"harina de trigo": "2", "sal fina": "2"}, "0")
    registry = ProviderRegistry({"cheap": lambda: cheap, "delivered": lambda: delivered})

    result = optimize_semantic_basket(
        registry,
        items=["harina de trigo", "sal fina"],
        postal_code="15001",
    )

    assert result["complete"] is True
    assert result["stores_used"] == ["delivered"]
    assert result["actual_total_text"] == "4.00"
    assert result["delivery_costs_complete"] is True


def test_optimizer_applies_explicit_constraints_and_optional_lines() -> None:
    provider = BasketProvider("one", {"harina de trigo": "1"}, "0")
    registry = ProviderRegistry({"one": lambda: provider})

    result = optimize_semantic_basket(
        registry,
        items=[
            {"query": "harina de trigo", "constraints": {"allergens": ["trigo"]}},
            {"query": "sal fina", "required": False},
        ],
        postal_code="15001",
    )

    assert result["complete"] is False
    assert result["required_missing"] == ["harina de trigo"]
    assert result["actual_total_text"] == "0.00"
