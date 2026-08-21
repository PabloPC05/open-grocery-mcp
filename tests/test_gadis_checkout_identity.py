from open_grocery_mcp.providers.gadis_account import GadisAccountClient


def test_checkout_cart_comparison_rejects_conflicting_product_ids() -> None:
    reviewed = {
        "lines": [
            {
                "product_id": "http-product-1",
                "name": "Leche entera 1 l",
                "quantity": 1,
            }
        ]
    }
    browser = {
        "lines": [
            {
                "product_id": "browser-product-2",
                "name": "Leche entera 1 l",
                "quantity": 1,
            }
        ]
    }

    assert GadisAccountClient._carts_equivalent(reviewed, browser) is False
