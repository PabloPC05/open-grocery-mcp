from decimal import Decimal

from open_grocery_mcp.comparison import compare_baskets, parse_basket, price_basket
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry


class StaticProvider(GroceryProvider):
    def __init__(self, key: str, catalogue: dict[str, list[Product]]) -> None:
        self.info = StoreInfo(
            key=key,
            label=key.title(),
            country="ES",
            languages=("es",),
            capabilities=("search", "compare"),
        )
        self.catalogue = catalogue

    def search(self, query: str, **_: object) -> list[Product]:
        return self.catalogue.get(query, [])


def p(store: str, product_id: str, name: str, price: str) -> Product:
    return Product(store=store, id=product_id, name=name, price=Decimal(price))


def test_price_basket_multiplies_requested_quantity() -> None:
    provider = StaticProvider(
        "alpha",
        {"leche 1 L": [p("alpha", "1", "Leche entera 1 L", "1.10")]},
    )
    items = parse_basket([{"query": "leche 1 L", "quantity": 3}])
    result = price_basket(provider, items)
    assert result["total_text"] == "3.30"
    assert result["complete"] is True


def test_comparison_ranks_complete_basket_before_cheaper_incomplete_one() -> None:
    alpha = StaticProvider(
        "alpha",
        {
            "leche": [p("alpha", "1", "Leche entera", "1.00")],
            "huevos": [p("alpha", "2", "Huevos 12 unidades", "2.50")],
        },
    )
    beta = StaticProvider(
        "beta",
        {"leche": [p("beta", "3", "Leche entera", "0.50")]},
    )
    registry = ProviderRegistry(
        factories={"alpha": lambda: alpha, "beta": lambda: beta}
    )
    result = compare_baskets(
        registry,
        items=["leche", "huevos"],
        stores=["beta", "alpha"],
    )
    assert result["ranking"][0]["store"] == "alpha"
    assert result["best_complete_store"] == "alpha"
    assert result["ranking"][1]["complete"] is False


def test_optional_missing_item_does_not_make_basket_incomplete() -> None:
    provider = StaticProvider("alpha", {})
    items = parse_basket([{"query": "optional", "required": False}])
    result = price_basket(provider, items)
    assert result["complete"] is True
    assert result["coverage"] == 0
