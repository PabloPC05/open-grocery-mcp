from __future__ import annotations

from decimal import Decimal

from open_grocery_mcp.models import Product
from open_grocery_mcp.promotions import (
    price_product_quantity,
    product_promotions,
)


def product(price: str, metadata: dict[str, object]) -> Product:
    return Product(
        store="test",
        id="p1",
        name="Producto",
        price=Decimal(price),
        metadata=metadata,
    )


def test_provider_previous_price_becomes_a_direct_discount() -> None:
    item = product(
        "2.00",
        {
            "promotion": {
                "current_price": 2,
                "previous_price": 3,
                "source": "previous_price_field",
            }
        },
    )

    result = price_product_quantity(item, Decimal("2"))

    assert result["effective_total_text"] == "4.00"
    assert result["regular_total_text"] == "6.00"
    assert result["savings_text"] == "2.00"
    assert result["applied_promotion"]["type"] == "direct_discount"


def test_gadis_fidelity_offer_requires_explicit_opt_in() -> None:
    item = product(
        "2.00",
        {
            "promotion": {
                "available": True,
                "current_price": 2,
                "offer_price": 1.5,
                "source": "fidelity_offer_price",
            }
        },
    )

    excluded = price_product_quantity(item, Decimal("2"))
    included = price_product_quantity(item, Decimal("2"), include_loyalty=True)

    assert excluded["effective_total_text"] == "4.00"
    assert excluded["applied_promotion"] is None
    assert excluded["warnings"] == ["loyalty promotion excluded"]
    assert included["effective_total_text"] == "3.00"


def test_eroski_explicit_two_for_one_prices_complete_cycles_only() -> None:
    item = product(
        "2.00",
        {
            "promotion": {
                "current_price": 2,
                "previous_price": 3,
                "label": "2x1",
                "type": "campaign",
                "quantity_mechanic": {"buy_quantity": 2, "pay_quantity": 1},
            }
        },
    )

    two = price_product_quantity(item, Decimal("2"))
    three = price_product_quantity(item, Decimal("3"))

    assert two["effective_total_text"] == "2.00"
    assert three["effective_total_text"] == "4.00"
    assert two["applied_promotion"]["type"] == "bundle_price"


def test_incomplete_second_unit_label_is_descriptive_not_actionable() -> None:
    item = product(
        "1.20",
        {
            "promotion": {
                "current_price": 1.2,
                "label": "2ª unidad",
                "quantity_mechanic": {"buy_quantity": 2},
            }
        },
    )

    result = price_product_quantity(item, Decimal("2"))

    assert result["effective_total_text"] == "2.40"
    assert result["applied_promotion"] is None
    assert product_promotions(item)[0]["actionable"] is False


def test_explicit_second_unit_percentage_is_quantity_aware() -> None:
    item = product(
        "6.79",
        {
            "promotion": {
                "current_price": 6.79,
                "label": "2ª unidad -70 %",
                "quantity_mechanic": {
                    "buy_quantity": 2,
                    "discount_percent": 70,
                },
            }
        },
    )

    one = price_product_quantity(item, Decimal("1"))
    two = price_product_quantity(item, Decimal("2"))

    assert one["effective_total_text"] == "6.79"
    assert two["effective_total_text"] == "8.83"
    assert two["savings_text"] == "4.75"
    assert two["applied_promotion"]["type"] == "second_unit_discount"


def test_froiz_flat_direct_and_quantity_fields_are_supported() -> None:
    direct = product(
        "1.50",
        {
            "order_price": 1.5,
            "base_price": 2,
            "promotion_type": "direct_discount",
            "price_source": "authenticated.order_price",
        },
    )
    quantity = product(
        "1.00",
        {
            "order_price": 1,
            "promotion_type": "quantity",
            "promotion_quantity": 3,
            "promotion_unit_price": 0.8,
        },
    )

    assert price_product_quantity(direct, Decimal("2"))["savings_text"] == "1.00"
    assert price_product_quantity(quantity, Decimal("2"))["effective_total_text"] == "2.00"
    assert price_product_quantity(quantity, Decimal("3"))["effective_total_text"] == "2.40"


def test_froiz_public_current_and_previous_prices_are_supported() -> None:
    item = product(
        "1.50",
        {
            "catalogue_current_price": 1.5,
            "catalogue_previous_price": 2,
            "promotion_type": "direct_discount",
            "price_source": "empathy.__prices.current.value",
        },
    )

    result = price_product_quantity(item, Decimal("1"))

    assert result["effective_total_text"] == "1.50"
    assert result["regular_total_text"] == "2.00"
    assert result["savings_text"] == "0.50"
    assert result["applied_promotion"]["discount_percent"] == 25.0


def test_malformed_or_personal_coupon_never_reduces_total() -> None:
    item = product(
        "2.00",
        {
            "promotion": {
                "current_price": 2,
                "previous_price": 5,
                "type": "coupon",
                "label": "Cupón personal",
            },
            "promotions": [{"type": "bundle_price", "bundle_price": "bad"}],
        },
    )

    result = price_product_quantity(item, Decimal("3"), include_loyalty=True)

    assert result["effective_total_text"] == "6.00"
    assert result["applied_promotion"] is None
    assert product_promotions(item)[0]["access_scope"] == "personal_coupon"
    assert "personal coupon" in result["warnings"][0]


def test_expired_future_or_unparseable_promotions_fail_closed() -> None:
    item = product(
        "2.00",
        {
            "promotions": [
                {
                    "type": "direct_discount",
                    "promotional_unit_price": 1,
                    "ends_at": "2000-01-01T00:00:00Z",
                },
                {
                    "type": "direct_discount",
                    "promotional_unit_price": 1,
                    "starts_at": "2999-01-01T00:00:00Z",
                },
                {
                    "type": "direct_discount",
                    "promotional_unit_price": 1,
                    "starts_at": "tomorrow-ish",
                },
            ]
        },
    )

    result = price_product_quantity(item, Decimal("1"))

    assert result["effective_total_text"] == "2.00"
    assert result["applied_promotion"] is None
    assert result["warnings"] == [
        "promotion has expired",
        "promotion has not started",
        "promotion validity could not be verified",
    ]
