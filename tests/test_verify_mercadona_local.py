from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal

from open_grocery_mcp.models import Product
from tools.verify_mercadona_local import verify


def _cart(*, version: int = 7, probe: bool = False) -> dict:
    lines = [
        {
            "product_id": "existing",
            "name": "Arroz",
            "quantity": 1.0,
            "unit_price": 1.50,
            "line_total": 1.50,
            "sources": [],
        }
    ]
    if probe:
        lines.append(
            {
                "product_id": "probe",
                "name": "Leche",
                "quantity": 1.0,
                "unit_price": 1.20,
                "line_total": 1.20,
                "sources": [],
            }
        )
    return {
        "store": "mercadona",
        "cart_id": "cart-1",
        "version": version,
        "total": 2.70 if probe else 1.50,
        "lines": lines,
    }


class FakeProvider:
    def __init__(self, *, ambiguous: bool = False, slots=None) -> None:
        self.cart = _cart()
        self.baseline = deepcopy(self.cart)
        self.ambiguous = ambiguous
        self.search_calls = 0
        self.preview_calls = 0
        self.commit_calls = 0
        self.forbidden_calls = 0
        self.closed = False
        self.slots = slots
        self.search_postal_code = None

    def account_status(self):
        return {"store": "mercadona", "authenticated": True}

    def real_cart(self):
        return deepcopy(self.cart)

    def delivery_addresses(self):
        return [
            {
                "id": "address-1",
                "label": "Calle Privada 99",
                "postal_code": "28050",
                "full_street_redacted": True,
            }
        ]

    def delivery_slots(self, address_id):
        assert address_id == "address-1"
        if self.slots is not None:
            return self.slots
        return [{"id": "slot-1", "available": True, "open": True, "price": 0.0}]

    def search(self, query, *, limit, postal_code, eco=False):
        del query, limit, eco
        self.search_postal_code = postal_code
        self.search_calls += 1
        return [
            Product(
                store="mercadona",
                id="probe",
                name="Leche",
                price=Decimal("1.20"),
                category="Lácteos",
            )
        ]

    def preview_cart_update(self, changes, *, mode, expected_version, max_total):
        del mode, max_total
        self.preview_calls += 1
        assert expected_version == self.cart["version"]
        requested = changes[0]
        quantity = Decimal(str(requested["quantity"]))
        desired = deepcopy(self.cart["lines"])
        desired = [line for line in desired if line["product_id"] != "probe"]
        if quantity > 0:
            desired.append(
                {
                    "product_id": "probe",
                    "name": "Leche",
                    "quantity": 1.0,
                    "unit_price": 1.20,
                    "line_total": 1.20,
                    "sources": [],
                }
            )
        return {
            "cart_id": "cart-1",
            "expected_cart_version": expected_version,
            "desired_lines": desired,
            "estimated_total": 2.70 if quantity > 0 else 1.50,
            "previous_lines": self.cart["lines"],
            "previous_total": self.cart["total"],
        }

    def commit_cart_update(self, plan):
        self.commit_calls += 1
        self.cart["lines"] = deepcopy(plan["desired_lines"])
        self.cart["total"] = plan["estimated_total"]
        self.cart["version"] += 1
        if self.ambiguous and self.commit_calls == 1:
            raise RuntimeError("simulated ambiguous response")
        return deepcopy(self.cart)

    def preview_checkout(self, **kwargs):
        del kwargs
        self.forbidden_calls += 1
        raise AssertionError("checkout must never be called")

    def create_checkout(self, plan):
        del plan
        self.forbidden_calls += 1
        raise AssertionError("checkout must never be called")

    def set_checkout_delivery(self, *args, **kwargs):
        del args, kwargs
        self.forbidden_calls += 1
        raise AssertionError("delivery selection must never be called")

    def submit_order(self, *args, **kwargs):
        del args, kwargs
        self.forbidden_calls += 1
        raise AssertionError("order must never be called")

    def close(self):
        self.closed = True


def _disable_order_opt_ins(monkeypatch):
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False)


def test_read_only_checks_session_cart_addresses_and_slots_without_search_or_write(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    provider = FakeProvider()

    code, report = verify(provider_factory=lambda: provider)

    assert code == 0
    assert report["ok"] is True
    assert report["steps"] == {
        "session_checked": True,
        "cart_snapshot": True,
        "addresses_read": True,
        "slots_read": True,
        "add_verified": False,
        "state_restored": None,
    }
    assert provider.search_calls == 0
    assert provider.preview_calls == 0
    assert provider.commit_calls == 0
    assert provider.forbidden_calls == 0
    assert provider.closed is True
    assert "Calle Privada" not in json.dumps(report, ensure_ascii=False)


def test_write_requires_both_explicit_opt_in_and_environment(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", raising=False)
    provider = FakeProvider()

    code, report = verify(
        allow_reversible_cart_write=True,
        provider_factory=lambda: provider,
    )

    assert code == 2
    assert report["ok"] is False
    assert provider.commit_calls == 0


def test_opt_in_adds_absent_ordinary_product_and_restores_exact_snapshot(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    provider = FakeProvider()
    baseline = deepcopy(provider.baseline)

    code, report = verify(
        allow_reversible_cart_write=True,
        provider_factory=lambda: provider,
    )

    assert code == 0
    assert report["ok"] is True
    assert report["retailer_write_performed"] is True
    assert report["mutation_attempted"] is True
    assert report["ambiguous_write"] is False
    assert report["steps"]["add_verified"] is True
    assert report["steps"]["state_restored"] is True
    assert provider.commit_calls == 2
    # The version is retailer-managed and may increase; all observable cart
    # contents and totals must return to the baseline.
    current = provider.real_cart()
    current["version"] = baseline["version"]
    assert current == baseline
    assert provider.forbidden_calls == 0


def test_ambiguous_write_is_reread_once_and_never_retried_or_cleaned_up(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    provider = FakeProvider(ambiguous=True)

    code, report = verify(
        allow_reversible_cart_write=True,
        provider_factory=lambda: provider,
    )

    assert code == 1
    assert report["ok"] is False
    assert report["ambiguous_write"] is True
    assert report["write_observation"] == "probe_present"
    assert report["steps"]["state_restored"] is None
    assert provider.commit_calls == 1
    assert provider.forbidden_calls == 0


def test_order_opt_in_blocks_even_read_only_mode(monkeypatch):
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")
    provider = FakeProvider()

    code, report = verify(provider_factory=lambda: provider)

    assert code == 2
    assert "order-submission" in report["reason"]
    assert provider.commit_calls == 0


def test_read_only_rejects_a_non_list_slot_contract(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    provider = FakeProvider(slots={"results": []})

    code, report = verify(provider_factory=lambda: provider)

    assert code == 1
    assert report["ok"] is False
    assert report["failure_stage"] == "slots"
    assert report["failure_type"] == "RuntimeError"
    assert provider.commit_calls == 0
    assert provider.forbidden_calls == 0


def test_non_empty_cart_without_positive_total_fails_closed_before_probe(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    provider = FakeProvider()
    provider.cart["total"] = 0

    code, report = verify(
        allow_reversible_cart_write=True,
        provider_factory=lambda: provider,
    )

    assert code == 1
    assert report["failure_stage"] == "cart_snapshot"
    assert report["mutation_attempted"] is False
    assert report["retailer_write_performed"] is False
    assert provider.search_calls == 0
    assert provider.commit_calls == 0


def test_probe_search_uses_postal_code_of_the_address_with_slots(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    provider = FakeProvider()
    provider.delivery_addresses = lambda: [
        {"postal_code": "99999"},
        {"id": "address-1", "postal_code": "28050"},
    ]

    code, report = verify(
        allow_reversible_cart_write=True,
        provider_factory=lambda: provider,
    )

    assert code == 0
    assert report["ok"] is True
    assert provider.search_postal_code == "28050"


def test_explicit_probe_postal_code_cannot_override_delivery_address(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    provider = FakeProvider()

    code, report = verify(
        allow_reversible_cart_write=True,
        postal_code="99999",
        provider_factory=lambda: provider,
    )

    assert code == 1
    assert report["failure_stage"] == "probe_selection"
    assert provider.search_calls == 0
    assert provider.commit_calls == 0


def test_probe_does_not_take_postal_code_from_an_unselected_address(monkeypatch):
    _disable_order_opt_ins(monkeypatch)
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    provider = FakeProvider()
    provider.delivery_addresses = lambda: [
        {"id": "address-1"},
        {"id": "address-2", "postal_code": "28050"},
    ]

    code, report = verify(
        allow_reversible_cart_write=True,
        provider_factory=lambda: provider,
    )

    assert code == 1
    assert report["failure_stage"] == "probe_selection"
    assert provider.search_calls == 0
    assert provider.commit_calls == 0
