from __future__ import annotations

from open_grocery_mcp.providers.froiz_pricing import (
    normalize_pricing,
    public_pricing_metadata,
)


def test_authenticated_direct_discount_is_normalized_from_order_and_base_price() -> None:
    metadata = normalize_pricing(
        {
            "order_price": 1.50,
            "base_price": "2.00",
            "offer": "Oferta semana",
        },
        price_source="authenticated.order_price",
    )

    assert metadata == {
        "price_source": "authenticated.order_price",
        "order_price": 1.5,
        "base_price": 2.0,
        "offer": "Oferta semana",
        "promotion_type": "direct_discount",
        "discount_amount": 0.5,
        "discount_percent": 25.0,
    }


def test_quantity_promotion_requires_explicit_kind_quantity_and_price() -> None:
    metadata = normalize_pricing(
        {
            "order_price": 1.00,
            "base_price": 1.00,
            "offer": {
                "type": "multi_buy",
                "quantity": 3,
                "unit_price": 0.80,
                "private_code": "must-not-escape",
            },
        },
        price_source="authenticated.order_price",
    )

    assert metadata["promotion_type"] == "quantity"
    assert metadata["quantity_promotion_type"] == "quantity"
    assert metadata["promotion_quantity"] == 3.0
    assert metadata["promotion_unit_price"] == 0.8
    assert "private_code" not in metadata


def test_ambiguous_offer_is_not_interpreted_as_a_quantity_rule() -> None:
    metadata = normalize_pricing(
        {"offer": {"label": "3x2", "quantity": 3, "price": 1.0}},
        price_source="authenticated.order_price",
    )

    assert metadata == {"price_source": "authenticated.order_price"}


def test_public_catalogue_maps_current_empathy_price_without_inventing_discount() -> None:
    metadata = public_pricing_metadata(
        {"__prices": {"current": {"value": "1.75"}}}
    )

    assert metadata == {
        "price_source": "empathy.__prices.current.value",
        "catalogue_current_price": 1.75,
    }


def test_public_catalogue_uses_explicit_previous_empathy_price() -> None:
    metadata = public_pricing_metadata(
        {
            "__prices": {
                "current": {"value": "1.50"},
                "previous": {"value": "2.00"},
            }
        }
    )

    assert metadata == {
        "price_source": "empathy.__prices.current.value",
        "catalogue_current_price": 1.5,
        "catalogue_previous_price": 2.0,
        "promotion_type": "direct_discount",
        "discount_amount": 0.5,
        "discount_percent": 25.0,
    }
