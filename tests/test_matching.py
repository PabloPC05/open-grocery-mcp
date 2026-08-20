from decimal import Decimal

from open_grocery_mcp.matching import parse_quantity, score_product, select_best
from open_grocery_mcp.models import Product


def product(name: str, price: str, *, available: bool = True) -> Product:
    return Product(
        store="test",
        id=name,
        name=name,
        price=Decimal(price),
        available=available,
    )


def test_quantity_parser_normalizes_mass_and_volume() -> None:
    assert parse_quantity("arroz 1 kg").value == Decimal("1000")
    assert parse_quantity("leche 100 cl").value == Decimal("1000")
    assert parse_quantity("agua 500 ml").dimension == "volume"


def test_matching_prefers_requested_format_over_cheapest_unrelated_hit() -> None:
    hits = [
        product("Bebida de avena 1 L", "0.50"),
        product("Leche entera 6 x 1 L", "5.80"),
        product("Leche entera 1 L", "1.05"),
    ]
    selected = select_best("leche entera 1 L", hits)
    assert selected is not None
    assert selected.product.name == "Leche entera 1 L"
    assert selected.score > 0.8


def test_max_unit_price_is_a_hard_constraint() -> None:
    hits = [product("Huevos camperos 12 unidades", "4.20")]
    assert select_best("huevos camperos 12 unidades", hits, max_unit_price=Decimal("4")) is None


def test_unavailable_products_are_not_selected() -> None:
    assert select_best("arroz", [product("Arroz redondo 1 kg", "1.00", available=False)]) is None


def test_score_explains_low_quantity_match() -> None:
    score, rationale = score_product(
        "yogur natural 4 unidades",
        product("Yogur natural 8 unidades", "2.00"),
    )
    assert 0 < score < 1
    assert any("quantity" in line for line in rationale)
