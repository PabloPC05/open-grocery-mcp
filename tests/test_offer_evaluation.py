from __future__ import annotations

from decimal import Decimal

from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.offer_evaluation import evaluate_offer_value
from open_grocery_mcp.providers.base import GroceryProvider


class StaticProvider(GroceryProvider):
    def __init__(self, products: list[Product], *, key: str = "mercadona") -> None:
        self.info = StoreInfo(
            key=key,
            label=key.title(),
            country="ES",
            languages=("es",),
            capabilities=("search", "compare"),
        )
        self.products = products
        self.calls = 0

    def search(self, query: str, **_: object) -> list[Product]:
        self.calls += 1
        return self.products


def p(
    product_id: str,
    name: str,
    price: str,
    *,
    store: str = "mercadona",
    brand: str | None = None,
    price_per_unit: str | None = None,
    unit: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Product:
    return Product(
        store=store,
        id=product_id,
        name=name,
        price=Decimal(price),
        brand=brand,
        price_per_unit=(
            Decimal(price_per_unit) if price_per_unit is not None else None
        ),
        unit=unit,
        metadata=metadata or {},
    )


def direct_offer(previous: str, current: str) -> dict[str, object]:
    return {
        "promotion": {
            "current_price": float(current),
            "previous_price": float(previous),
        }
    }


def test_offer_loses_to_cheaper_private_label_with_one_catalogue_call() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Leche entera Marca A 1 L",
                "1.20",
                brand="Marca A",
                price_per_unit="1.20",
                unit="L",
                metadata=direct_offer("1.60", "1.20"),
            ),
            p(
                "white",
                "Leche entera Hacendado 1 L",
                "0.95",
                brand="Hacendado",
                price_per_unit="0.95",
                unit="L",
            ),
            p(
                "bulk",
                "Leche entera marca blanca 5 L",
                "3.00",
                price_per_unit="0.60",
                unit="L",
            ),
        ]
    )

    result = evaluate_offer_value(provider, query="leche entera")

    assert provider.calls == 1
    assert result["counts"] == {
        "worthwhile": 0,
        "not_worthwhile": 1,
        "unverified": 0,
    }
    row = result["not_worthwhile_offers"][0]
    assert row["cheapest_similar"]["product"]["id"] == "white"
    assert row["cheapest_similar"]["private_label"] is True
    assert row["advantage_percent"] < 0


def test_offer_beats_private_label_on_comparable_litre_price() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Leche entera Marca A 1 L",
                "0.80",
                brand="Marca A",
                price_per_unit="0.80",
                unit="L",
                metadata=direct_offer("1.60", "0.80"),
            ),
            p(
                "white",
                "Leche entera Hacendado 1 L",
                "0.95",
                brand="Hacendado",
                price_per_unit="0.95",
                unit="L",
            ),
        ]
    )

    row = evaluate_offer_value(provider, query="leche entera")[
        "worthwhile_offers"
    ][0]

    assert row["comparison_basis"] == "EUR/L"
    assert row["advantage_percent_text"] == "15.8%"


def test_auto_quantity_evaluates_two_for_one_against_two_alternatives() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Leche entera Marca A 1 L",
                "1.50",
                brand="Marca A",
                price_per_unit="1.50",
                unit="L",
                metadata={
                    "promotions": [
                        {
                            "type": "bundle_price",
                            "required_quantity": 2,
                            "bundle_price": 1.5,
                        }
                    ]
                },
            ),
            p(
                "white",
                "Leche entera Hacendado 1 L",
                "1.00",
                brand="Hacendado",
                price_per_unit="1.00",
                unit="L",
            ),
        ]
    )

    row = evaluate_offer_value(provider, query="leche entera")[
        "worthwhile_offers"
    ][0]

    assert row["evaluation_quantity"] == 2
    assert row["pricing"]["effective_total_text"] == "1.50"
    assert row["offered_comparable_price_text"] == "0.75"


def test_broad_query_does_not_compare_different_oil_types() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Aceite oliva virgen extra Marca A 1 L",
                "5.00",
                brand="Marca A",
                price_per_unit="5",
                unit="L",
                metadata=direct_offer("7", "5"),
            ),
            p(
                "sunflower",
                "Aceite girasol marca blanca 1 L",
                "1.50",
                price_per_unit="1.5",
                unit="L",
            ),
            p(
                "smooth",
                "Aceite oliva suave marca blanca 5 L",
                "15.00",
                price_per_unit="3",
                unit="L",
            ),
            p(
                "olive",
                "Aceite oliva virgen extra marca blanca 1 L",
                "5.50",
                price_per_unit="5.5",
                unit="L",
            ),
        ]
    )

    row = evaluate_offer_value(provider, query="aceite")["worthwhile_offers"][0]

    assert row["cheapest_similar"]["product"]["id"] == "olive"


def test_broad_milk_query_keeps_fat_variant() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Leche semidesnatada Marca A 1 L",
                "1.00",
                price_per_unit="1",
                unit="L",
                metadata=direct_offer("1.5", "1"),
            ),
            p(
                "skimmed",
                "Leche desnatada marca blanca 1 L",
                "0.70",
                price_per_unit="0.7",
                unit="L",
            ),
            p(
                "semi",
                "Leche semidesnatada marca blanca 1 L",
                "1.10",
                price_per_unit="1.1",
                unit="L",
            ),
        ]
    )

    row = evaluate_offer_value(provider, query="leche")["worthwhile_offers"][0]

    assert row["cheapest_similar"]["product"]["id"] == "semi"


def test_descriptive_offer_can_be_checked_by_current_shelf_price() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Café soluble IFA 200 g",
                "3.00",
                price_per_unit="15",
                unit="kg",
                metadata={
                    "promotions": [
                        {
                            "type": "unknown",
                            "description": "Folleto",
                        }
                    ]
                },
            ),
            p(
                "other",
                "Café soluble Marca A 200 g",
                "4.00",
                price_per_unit="20",
                unit="kg",
            ),
        ],
        key="gadis",
    )

    row = evaluate_offer_value(provider, query="café soluble")[
        "worthwhile_offers"
    ][0]

    assert row["promotion_mechanic_applied"] is False
    assert row["advantage_percent_text"] == "25.0%"


def test_offer_without_reliable_comparison_basis_is_unverified() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Preparado especial Marca A",
                "2.00",
                metadata=direct_offer("3", "2"),
            ),
            p("other", "Preparado especial Marca B", "1.00"),
        ]
    )

    result = evaluate_offer_value(provider, query="preparado especial")

    assert result["counts"]["unverified"] == 1
    assert "reliable" in result["unverified_offers"][0]["reason"]


def test_prepared_food_offer_uses_same_filling_not_cheaper_other_filling() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Croquetas de jamón Marca A 400 g",
                "4.00",
                price_per_unit="10",
                unit="kg",
                metadata=direct_offer("5", "4"),
            ),
            p(
                "cod",
                "Croquetas de bacalao marca blanca 400 g",
                "2.00",
                price_per_unit="5",
                unit="kg",
            ),
            p(
                "ham",
                "Croquetas de jamón marca blanca 400 g",
                "4.40",
                price_per_unit="11",
                unit="kg",
            ),
        ]
    )

    row = evaluate_offer_value(provider, query="croquetas")["worthwhile_offers"][0]

    assert row["cheapest_similar"]["product"]["id"] == "ham"


def test_prepared_dish_query_rejects_cheaper_ingredient_kit() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Paella de marisco congelada 500 g",
                "4.00",
                price_per_unit="8",
                unit="kg",
                metadata=direct_offer("5", "4"),
            ),
            p(
                "kit",
                "Preparado para paella de marisco 500 g",
                "2.00",
                price_per_unit="4",
                unit="kg",
            ),
        ]
    )

    result = evaluate_offer_value(provider, query="paella de marisco")

    assert result["products_rejected_by_query"] == 1
    assert result["counts"]["unverified"] == 1


def test_pantry_offer_ignores_cheaper_flour_from_another_source() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Harina de trigo Marca A 1 kg",
                "1.20",
                price_per_unit="1.20",
                unit="kg",
                metadata=direct_offer("1.60", "1.20"),
            ),
            p(
                "chickpea",
                "Harina de garbanzo 1 kg",
                "0.80",
                price_per_unit="0.80",
                unit="kg",
            ),
            p(
                "wheat",
                "Harina de trigo marca blanca 1 kg",
                "1.30",
                price_per_unit="1.30",
                unit="kg",
            ),
        ]
    )

    row = evaluate_offer_value(provider, query="harina")["worthwhile_offers"][0]

    assert row["cheapest_similar"]["product"]["id"] == "wheat"


def test_cleaning_offer_does_not_compare_laundry_and_dishwashing() -> None:
    provider = StaticProvider(
        [
            p(
                "offer",
                "Detergente ropa líquido Marca A 1 L",
                "2.00",
                price_per_unit="2",
                unit="L",
                metadata=direct_offer("3", "2"),
            ),
            p(
                "dish",
                "Lavavajillas a mano líquido 1 L",
                "1.00",
                price_per_unit="1",
                unit="L",
            ),
            p(
                "laundry",
                "Detergente ropa líquido marca blanca 1 L",
                "2.20",
                price_per_unit="2.20",
                unit="L",
            ),
        ]
    )

    row = evaluate_offer_value(provider, query="detergente")["worthwhile_offers"][0]

    assert row["cheapest_similar"]["product"]["id"] == "laundry"
