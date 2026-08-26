from copy import deepcopy
from decimal import Decimal

from open_grocery_mcp.models import Product
from tools.verify_mercadona_checkout_review_local import verify


class FakeMercadona:
    def __init__(self) -> None:
        self.cart = {"cart_id": "cart", "version": 1, "total": 0, "lines": []}
        self.closed = False

    def account_status(self):
        return {"authenticated": True}

    def real_cart(self):
        return deepcopy(self.cart)

    def delivery_addresses(self):
        return [{"id": "address", "postal_code": "15001"}]

    def delivery_slots(self, address_id):
        assert address_id == "address"
        return [{"id": "slot", "available": True, "open": True}]

    def search(self, query, *, limit, postal_code):
        assert (query, limit, postal_code) == ("leche", 25, "15001")
        return [
            Product(
                store="mercadona",
                id="milk",
                name="Leche entera",
                price=Decimal("2"),
                category="lacteos",
            )
        ]

    def preview_cart_update(self, changes, **kwargs):
        return {"changes": deepcopy(changes), **kwargs}

    def commit_cart_update(self, plan):
        change = plan["changes"][0]
        self.cart["version"] += 1
        if Decimal(str(change["quantity"])) == 0:
            self.cart.update(total=0, lines=[])
        else:
            self.cart.update(
                total=2,
                lines=[
                    {
                        "product_id": "milk",
                        "quantity": 1,
                        "unit_price": 2,
                        "line_total": 2,
                        "sources": [],
                    }
                ],
            )
        return self.real_cart()

    def preview_checkout(self, **kwargs):
        return {"cart": self.real_cart(), **kwargs}

    def create_checkout(self, plan):
        assert plan["cart"]["lines"]
        return {"checkout_id": "checkout", "total": 2}

    def set_checkout_delivery(self, checkout_id, **kwargs):
        assert checkout_id == "checkout"
        return {
            "checkout_id": checkout_id,
            "address_id": str(kwargs["address_id"]),
            "slot_id": kwargs["slot_id"],
            "total": 2,
        }

    def open_human_review(self, **kwargs):
        assert kwargs["checkout_review"] is True
        return {
            "window_opened": True,
            "network_write_guard": "all_non_get_blocked",
            "review_path_verified": True,
            "non_get_requests_blocked": 1,
        }

    def close(self):
        self.closed = True


def test_mercadona_checkout_review_is_guarded_and_restores_probe(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False)
    provider = FakeMercadona()

    code, report = verify(
        max_added_value=Decimal("5"),
        max_total=Decimal("10"),
        timeout_seconds=30,
        provider_factory=lambda: provider,
    )

    assert code == 0
    assert report["ok"] is True
    assert report["checkout_created"] is True
    assert report["delivery_selected"] is True
    assert report["all_non_get_blocked"] is True
    assert report["probe_removed"] is True
    assert provider.cart["lines"] == []
    assert provider.closed is True
