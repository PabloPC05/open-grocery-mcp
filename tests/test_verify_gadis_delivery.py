"""Regression tests for the live delivery verifier's safety flow."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from verify_gadis_delivery_local import verify  # noqa: E402


@pytest.fixture(autouse=True)
def _writes_enabled(monkeypatch):
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv(
        "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False
    )


class FakeHttp:
    def __init__(self) -> None:
        self.calendar = [
            {"id": "slot-1", "date": "2026-08-30", "available": True, "active": True}
        ]
        self.cart_addresses: list[dict[str, Any]] = []
        self.client_addresses_rows: list[dict[str, Any]] = []
        self.raw_delivery_date = ""
        self.fail_checkout = False
        self.raw_schedule_range: str | None = None
        self.schedule_writes = 0
        self.checkout_calls = 0
        self._baseline = {
            "id": "cart-1",
            "store_id": "store-7",
            "postal_code": "28050",
            "products": [{"product_id": "p-1", "amount": 1}],
            "total_cart_price": 2.5,
            "total_products": 1,
        }

    def _raw(self) -> dict[str, Any]:
        raw = dict(self._baseline)
        raw["delivery_date"] = self.raw_delivery_date or None
        raw["schedule_range_id"] = self.raw_schedule_range
        return raw

    def read_cart(self) -> dict[str, Any]:
        return self._raw()

    def delivery_slots(self, postal_code=None, *, store_id=None, **_):
        return [dict(s) for s in self.calendar]

    def addresses(self, cart_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.cart_addresses]

    def client_addresses(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.client_addresses_rows]

    def update_schedule(self, cart_id, store_id, *, delivery_date, schedule_range_id):
        if not schedule_range_id:
            raise AssertionError("refusing to write an empty slot")
        self.schedule_writes += 1
        self.raw_delivery_date = str(delivery_date)
        self.raw_schedule_range = str(schedule_range_id)
        return {**self._baseline, "cart_id": cart_id}

    def delete_schedule(self, cart_id):
        self.schedule_writes += 1
        self.raw_delivery_date = ""
        self.raw_schedule_range = None
        return None

    def create_checkout(self, cart_id, store_id, **kwargs):
        assert kwargs.get("shipping_address_id"), "checkout needs an address id"
        self.checkout_calls += 1
        if self.fail_checkout:
            raise RuntimeError("simulated checkout failure")
        return {
            "checkout_present": True,
            "removed_products": [],
            "has_product_price_changes": False,
            "order_placed": False,
        }


class FakeAccount:
    def __init__(self, http: FakeHttp) -> None:
        self._http = http


class FakeProvider:
    def __init__(self, http: FakeHttp) -> None:
        self._account = FakeAccount(http)

    def account_status(self):
        return {"authenticated": True, "account_backend": "gadis_http"}

    def real_cart(self):
        return {
            "cart_backend": "gadis_http",
            "browser_driven": False,
            "cart_id": "cart-1",
            "store_id": "store-7",
            "version": 1,
        }

    def close(self):
        pass


def _factory(http: FakeHttp):
    return lambda: FakeProvider(http)


def test_checkout_create_without_addresses_performs_no_write() -> None:
    http = FakeHttp()
    http.client_addresses_rows = []
    code, report = verify(
        allow_reversible_schedule_write=True,
        allow_checkout_create=True,
        provider_factory=_factory(http),
    )
    assert code == 1
    assert "nothing was written" in (report.get("reason") or "")
    assert report["retailer_write_performed"] is False
    assert http.schedule_writes == 0
    assert http.checkout_calls == 0


def test_failed_checkout_still_restores_schedule_state() -> None:
    http = FakeHttp()
    http.client_addresses_rows = [{"id": "addr-1", "owner": "CLIENT"}]
    http.fail_checkout = True
    code, report = verify(
        allow_reversible_schedule_write=True,
        allow_checkout_create=True,
        provider_factory=_factory(http),
    )
    assert code == 1
    assert report["steps"]["schedule_applied"] is True
    assert report["steps"]["state_restored"] is True
    assert report["retailer_write_performed"] is True
    assert http.checkout_calls == 1
    # apply + cleanup delete = at least two schedule operations.
    assert http.schedule_writes >= 2


def test_reversible_schedule_round_trip_is_clean() -> None:
    http = FakeHttp()
    code, report = verify(
        allow_reversible_schedule_write=True,
        provider_factory=_factory(http),
    )
    assert code == 0 and report["ok"] is True
    assert report["steps"]["schedule_applied"] is True
    assert report["steps"]["schedule_removed"] is True
    assert report["steps"]["state_restored"] is True


def test_client_addresses_satisfy_checkout_prerequisite() -> None:
    http = FakeHttp()
    http.client_addresses_rows = [{"id": "addr-9", "owner": "CLIENT"}]
    code, report = verify(
        allow_reversible_schedule_write=True,
        allow_checkout_create=True,
        provider_factory=_factory(http),
    )
    assert code == 0 and report["ok"] is True
    assert report["steps"]["checkout_created"] is True
    assert report.get("checkout_order_placed") is False
