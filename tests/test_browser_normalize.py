from decimal import Decimal

from open_grocery_mcp.providers.browser_config import GADIS_BROWSER_CONFIG, FROIZ_BROWSER_CONFIG
from open_grocery_mcp.providers.browser_normalize import (
    cart_version,
    is_restricted_product,
    normalize_addresses,
    normalize_cart_payload,
    normalize_slots,
    same_line_identity,
)


def test_normalize_cart_payload_prefers_cart_shape():
    payload = {
        "unrelated": {"products": [{"name": "banner"}]},
        "cart": {
            "id": "c1",
            "version": 7,
            "lines": [
                {
                    "quantity": 2,
                    "product": {
                        "id": "p1",
                        "display_name": "Leche entera 1 L",
                        "price_instructions": {"unit_price": "1.05"},
                        "share_url": "https://example.test/product/leche?secret=x",
                    },
                }
            ],
            "summary": {"total": "2.10"},
        },
    }
    cart = normalize_cart_payload(payload, "demo")
    assert cart is not None
    assert cart["version"] == 7
    assert cart["total_text"] == "2.10"
    assert cart["lines"][0]["url"] == "https://example.test/product/leche"


def test_address_and_slot_normalization_redacts_street():
    payload = {
        "addresses": [
            {"id": 12, "street": "Rúa Privada 8", "postal_code": "15706", "city": "Santiago", "default": True}
        ],
        "slots": [
            {"id": "s1", "start": "2026-08-21T18:00", "end": "2026-08-21T20:00", "available": True, "price": "4.50"}
        ],
    }
    addresses = normalize_addresses(payload)
    assert addresses == [{"id": "12", "label": "15706 · Santiago", "postal_code": "15706", "city": "Santiago", "street_redacted": True, "default": True}]
    slots = normalize_slots(payload)
    assert slots[0]["id"] == "s1"
    assert slots[0]["price_text"] == "4.50"


def test_cart_version_is_order_independent_and_restrictions_apply():
    a = [{"product_id": "1", "quantity": 1, "unit_price": 2}, {"product_id": "2", "quantity": 3, "unit_price": 1}]
    b = list(reversed(a))
    assert cart_version(a, Decimal("5")) == cart_version(b, Decimal("5"))
    assert is_restricted_product("Vino tinto 75 cl")
    assert not is_restricted_product("Leche entera 1 L")


def test_navigation_patterns_do_not_blur_add_continue_and_submit_actions():
    for config in (GADIS_BROWSER_CONFIG, FROIZ_BROWSER_CONFIG):
        assert "comprar" not in {pattern.casefold() for pattern in config.add_patterns}
        assert "confirmar" not in {pattern.casefold() for pattern in config.continue_patterns}
        assert any("pedido" in pattern for pattern in config.submit_patterns)


def test_same_line_identity_uses_id_url_then_conservative_name():
    assert same_line_identity(
        {"product_id": "p1", "name": "Leche"},
        {"product_id": "p1", "name": "Otro texto"},
    )
    assert same_line_identity(
        {"url": "https://shop.test/product/leche?token=x"},
        {"url": "https://shop.test/product/leche"},
    )
    assert not same_line_identity(
        {"name": "Pan"},
        {"name": "Pan rallado"},
    )
    assert same_line_identity(
        {"name": "Leche entera 1 litro"},
        {"name": "Leche entera 1 litro marca blanca"},
    )
