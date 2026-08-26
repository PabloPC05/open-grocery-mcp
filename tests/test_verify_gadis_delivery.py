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
        self.raw_address_id: str | None = None
        self.raw_address_owner: str | None = None
        self.raw_postal_code = "28050"
        self.raw_delivery_type = "HOME_DELIVERY"
        self.raw_comments = ""
        self.fail_summary = False
        self.raw_schedule_range: str | None = None
        self.schedule_writes = 0
        self.checkout_calls = 0
        self.fail_schedule_update = False
        self.mutate_cart_on_schedule_update = False
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
        raw["shipping_address_id"] = self.raw_address_id
        raw["shipping_address_owner"] = self.raw_address_owner
        raw["postal_code"] = self.raw_postal_code
        raw["delivery_type"] = self.raw_delivery_type
        raw["comments"] = self.raw_comments
        return raw

    def read_cart(self) -> dict[str, Any]:
        return self._raw()

    def delivery_slots(self, postal_code=None, *, store_id=None, **_):
        return [dict(s) for s in self.calendar]

    def addresses(self, cart_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.cart_addresses]

    def client_addresses(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.client_addresses_rows]

    def update_schedule(
        self,
        cart_id,
        store_id,
        *,
        delivery_date,
        schedule_range_id,
        postal_code=None,
        shipping_address_id=None,
        shipping_address_owner=None,
    ):
        if not schedule_range_id:
            raise AssertionError("refusing to write an empty slot")
        self.schedule_writes += 1
        if self.fail_schedule_update:
            raise RuntimeError("private retailer error")
        self.raw_delivery_date = str(delivery_date)
        self.raw_schedule_range = str(schedule_range_id)
        if postal_code:
            self.raw_postal_code = str(postal_code)
        if shipping_address_id is not None:
            self.raw_address_id = str(shipping_address_id)
            self.raw_address_owner = shipping_address_owner
        if self.mutate_cart_on_schedule_update:
            self._baseline["products"].append({"product_id": "unexpected", "amount": 1})
        return {**self._baseline, "cart_id": cart_id}

    def delete_schedule(self, cart_id):
        self.schedule_writes += 1
        self.raw_delivery_date = ""
        self.raw_schedule_range = None
        return None

    def prepare_checkout_summary(self, cart_id, store_id, **kwargs):
        assert kwargs.get("shipping_address_id"), "summary needs an address id"
        self.checkout_calls += 1
        if self.fail_summary:
            raise RuntimeError("simulated summary failure")
        self.raw_address_id = str(kwargs["shipping_address_id"])
        self.raw_address_owner = kwargs.get("shipping_address_owner")
        self.raw_delivery_date = str(kwargs["delivery_date"])
        self.raw_schedule_range = str(kwargs["schedule_range_id"])
        if kwargs.get("postal_code"):
            self.raw_postal_code = str(kwargs["postal_code"])
        return {
            "summary_prepared": True,
        }

    def restore_cart_context(self, baseline):
        self.raw_delivery_date = str(baseline.get("delivery_date") or "")
        self.raw_schedule_range = baseline.get("schedule_range_id")
        self.raw_address_id = baseline.get("shipping_address_id")
        self.raw_address_owner = baseline.get("shipping_address_owner")
        self.raw_postal_code = str(baseline.get("postal_code") or "")
        self.raw_delivery_type = str(baseline.get("delivery_type") or "")
        self.raw_comments = str(baseline.get("comments") or "")
        return {"cart_id": baseline.get("id")}

    def create_checkout(self, *args, **kwargs):
        raise AssertionError("payment-bearing checkout must never be called")


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


def test_checkout_summary_without_addresses_performs_no_write() -> None:
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


def test_failed_checkout_summary_still_restores_full_delivery_state() -> None:
    http = FakeHttp()
    http.client_addresses_rows = [{"id": "addr-1", "owner": "CLIENT"}]
    http.fail_summary = True
    code, report = verify(
        allow_reversible_schedule_write=True,
        allow_checkout_create=True,
        provider_factory=_factory(http),
    )
    assert code == 1
    assert report["steps"]["schedule_applied"] is True
    assert report["steps"]["state_restored"] is True
    assert report["failure_stage"] == "checkout_summary_prepare"
    assert report["failure_type"] == "RuntimeError"
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


def test_client_addresses_satisfy_checkout_summary_prerequisite() -> None:
    http = FakeHttp()
    http.client_addresses_rows = [{"id": "addr-9", "owner": "CLIENT"}]
    code, report = verify(
        allow_reversible_schedule_write=True,
        allow_checkout_create=True,
        provider_factory=_factory(http),
    )
    assert code == 0 and report["ok"] is True
    assert report["steps"]["checkout_summary_prepared"] is True
    assert report["steps"]["checkout_created"] is False


def test_failed_schedule_update_never_triggers_cleanup() -> None:
    http = FakeHttp()
    http.fail_schedule_update = True

    code, report = verify(
        allow_reversible_schedule_write=True,
        provider_factory=_factory(http),
    )

    assert code == 1
    assert report["failure_stage"] == "schedule_update"
    assert report["failure_type"] == "RuntimeError"
    assert report["retailer_write_attempted"] is True
    assert report["steps"]["schedule_applied"] is False
    assert report["cleanup_skipped"] is True
    assert http.schedule_writes == 1
    assert "private retailer error" not in str(report)


def test_schedule_cleanup_is_skipped_when_cart_changes_during_update() -> None:
    http = FakeHttp()
    http.mutate_cart_on_schedule_update = True

    code, report = verify(
        allow_reversible_schedule_write=True,
        provider_factory=_factory(http),
    )

    assert code == 1
    assert report["failure_stage"] == "schedule_verify"
    assert report["steps"]["schedule_applied"] is False
    assert report["cleanup_skipped"] is True
    # Only the attempted update reached the fake retailer; DELETE cleanup was
    # not issued after the cart fingerprint changed.
    assert http.schedule_writes == 1
