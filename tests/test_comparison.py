from decimal import Decimal

from open_grocery_mcp.comparison import compare_baskets, parse_basket, price_basket
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry


class StaticProvider(GroceryProvider):
    def __init__(
        self,
        key: str,
        catalogue: dict[str, list[Product]],
        *,
        delivery: dict[str, object] | None = None,
    ) -> None:
        self.info = StoreInfo(
            key=key,
            label=key.title(),
            country="ES",
            languages=("es",),
            capabilities=("search", "compare"),
        )
        self.catalogue = catalogue
        self.delivery = delivery

    def search(self, query: str, **_: object) -> list[Product]:
        return self.catalogue.get(query, [])

    def delivery_coverage(self, postal_code: str) -> dict[str, object]:
        if self.delivery is None:
            raise AssertionError("delivery_coverage should not be called")
        return {"postal_code": postal_code, **self.delivery}


class NoDeliveryProvider(StaticProvider):
    delivery_coverage = None  # type: ignore[assignment]


def p(store: str, product_id: str, name: str, price: str) -> Product:
    return Product(store=store, id=product_id, name=name, price=Decimal(price))


def test_price_basket_multiplies_requested_quantity() -> None:
    provider = NoDeliveryProvider(
        "alpha",
        {"leche 1 L": [p("alpha", "1", "Leche entera 1 L", "1.10")]},
    )
    items = parse_basket([{"query": "leche 1 L", "quantity": 3}])
    result = price_basket(provider, items)
    assert result["total_text"] == "3.30"
    assert result["complete"] is True
    assert result["checkout_eligible"] is None


def test_price_basket_includes_verified_delivery_policy() -> None:
    provider = StaticProvider(
        "alpha",
        {"leche": [p("alpha", "1", "Leche entera", "10.00")]},
        delivery={
            "store_id": "store-a",
            "shipping_costs": 4.9,
            "minimum_order_quantity": 25,
            "minimum_shipping_free": 90,
        },
    )
    items = parse_basket([{"query": "leche", "quantity": 3}])
    result = price_basket(provider, items, postal_code="28050")
    assert result["subtotal_text"] == "30.00"
    assert result["delivery"]["applied_delivery_fee_text"] == "4.90"
    assert result["estimated_checkout_total_text"] == "34.90"
    assert result["checkout_eligible"] is True
    assert "delivery fees" not in result["comparison_excludes"]


def test_delivery_policy_marks_basket_below_minimum() -> None:
    provider = StaticProvider(
        "alpha",
        {"leche": [p("alpha", "1", "Leche entera", "10.00")]},
        delivery={
            "shipping_costs": 4.9,
            "minimum_order_quantity": 25,
            "minimum_shipping_free": 90,
        },
    )
    result = price_basket(
        provider,
        parse_basket(["leche"]),
        postal_code="28050",
    )
    assert result["complete"] is True
    assert result["checkout_eligible"] is False
    assert any("below the retailer minimum" in warning for warning in result["warnings"])


def test_free_delivery_threshold_is_applied() -> None:
    provider = StaticProvider(
        "alpha",
        {"rice": [p("alpha", "1", "Rice 1 kg", "50.00")]},
        delivery={
            "shipping_costs": 4.9,
            "minimum_order_quantity": 25,
            "minimum_shipping_free": 90,
        },
    )
    result = price_basket(
        provider,
        parse_basket([{"query": "rice", "quantity": 2}]),
        postal_code="28050",
    )
    assert result["delivery"]["applied_delivery_fee_text"] == "0.00"
    assert result["estimated_checkout_total_text"] == "100.00"


def test_comparison_ranks_complete_basket_before_cheaper_incomplete_one() -> None:
    alpha = NoDeliveryProvider(
        "alpha",
        {
            "leche": [p("alpha", "1", "Leche entera", "1.00")],
            "huevos": [p("alpha", "2", "Huevos 12 unidades", "2.50")],
        },
    )
    beta = NoDeliveryProvider(
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


def test_comparison_uses_estimated_checkout_total_when_available() -> None:
    products = {"leche": [p("alpha", "1", "Leche entera", "10.00")]}
    alpha = StaticProvider(
        "alpha",
        products,
        delivery={
            "shipping_costs": 6,
            "minimum_order_quantity": 0,
            "minimum_shipping_free": 0,
        },
    )
    beta = StaticProvider(
        "beta",
        {"leche": [p("beta", "2", "Leche entera", "12.00")]},
        delivery={
            "shipping_costs": 0,
            "minimum_order_quantity": 0,
            "minimum_shipping_free": 0,
        },
    )
    registry = ProviderRegistry(
        factories={"alpha": lambda: alpha, "beta": lambda: beta}
    )
    result = compare_baskets(
        registry,
        items=["leche"],
        stores=["alpha", "beta"],
        postal_code="28050",
    )
    assert result["ranking"][0]["store"] == "beta"
    assert result["best_estimated_checkout_store"] == "beta"


def test_optional_missing_item_does_not_make_basket_incomplete() -> None:
    provider = NoDeliveryProvider("alpha", {})
    items = parse_basket([{"query": "optional", "required": False}])
    result = price_basket(provider, items)
    assert result["complete"] is True
    assert result["coverage"] == 0
