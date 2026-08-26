from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

from tools.verify_gadis_checkout_review_local import verify


class FakeHTTP:
    def __init__(self) -> None:
        self.raw = {
            "id": "cart-1",
            "store_id": "store-1",
            "products": [{"product_id": "rice", "amount": 1}],
            "total_cart_price": 2,
            "total_products": 1,
            "delivery_date": "",
            "schedule_range_id": "",
            "shipping_address_id": "",
            "shipping_address_owner": "",
            "postal_code": "",
            "delivery_type": "HOME_DELIVERY",
            "comments": "",
        }

    def read_cart(self):
        return deepcopy(self.raw)

    def delete_schedule(self, cart_id):
        assert cart_id == "cart-1"
        self.raw["delivery_date"] = ""
        self.raw["schedule_range_id"] = ""

    def restore_cart_context(self, baseline):
        self.raw = deepcopy(baseline)


class FakeProvider:
    def __init__(self, *, guarded: bool = True) -> None:
        self.http = FakeHTTP()
        self._account = SimpleNamespace(_http=self.http)
        self.guarded = guarded
        self.closed = False

    def real_cart(self):
        return {"version": 7, "total": 2, "lines": [{"product_id": "rice"}]}

    def delivery_addresses(self):
        return [{"id": "address-1", "owner": "owner"}]

    def delivery_slots(self, address_id):
        assert address_id == "address-1"
        return [{"id": "slot-1", "date": "2026-08-26", "available": True}]

    def preview_checkout(self, *, expected_version, max_total):
        assert expected_version == 7
        assert max_total == Decimal("5")
        return {"expected_cart_version": 7, "max_total": 5}

    def create_checkout(self, plan):
        delivery = plan["delivery"]
        self.http.raw.update(
            {
                "delivery_date": delivery["delivery_date"],
                "schedule_range_id": delivery["schedule_range_id"],
                "shipping_address_id": delivery["shipping_address_id"],
                "shipping_address_owner": delivery["shipping_address_owner"],
            }
        )
        return {"checkout_id": "review-1", "total": 2}

    def get_checkout(self, checkout_id):
        assert checkout_id == "review-1"
        return {"checkout_id": checkout_id, "total": 2}

    def open_human_review(self, **kwargs):
        assert kwargs == {
            "checkout_id": "review-1",
            "checkout_review": True,
            "timeout_seconds": 30,
        }
        return {
            "window_opened": True,
            "network_write_guard": (
                "all_non_get_blocked" if self.guarded else "missing"
            ),
            "review_path_verified": True,
            "non_get_requests_blocked": 2,
            "review_url": "https://www.gadisline.com/checkout",
        }

    def close(self):
        self.closed = True


def test_checkout_review_reaches_guarded_window_and_restores(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False)
    provider = FakeProvider()

    code, report = verify(
        max_total=Decimal("5"),
        timeout_seconds=30,
        provider_factory=lambda: provider,
    )

    assert code == 0
    assert report["ok"] is True
    assert report["checkout_review_reached"] is True
    assert report["all_non_get_blocked"] is True
    assert report["state_restored"] is True
    assert provider.http.raw["delivery_date"] == ""
    assert provider.closed is True


def test_checkout_review_fails_closed_without_network_guard_and_restores(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False)
    provider = FakeProvider(guarded=False)

    code, report = verify(
        max_total=Decimal("5"),
        timeout_seconds=30,
        provider_factory=lambda: provider,
    )

    assert code == 1
    assert report["ok"] is False
    assert report["all_non_get_blocked"] is False
    assert report["state_restored"] is True


def test_checkout_review_refuses_order_opt_in_before_provider_creation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")
    called = False

    def provider_factory():
        nonlocal called
        called = True
        return FakeProvider()

    code, report = verify(
        max_total=Decimal("5"),
        timeout_seconds=30,
        provider_factory=provider_factory,
    )

    assert code == 2
    assert report["ok"] is False
    assert called is False
