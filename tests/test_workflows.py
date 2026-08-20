from __future__ import annotations

from decimal import Decimal

import pytest

from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.errors import OrderApprovalRequired, RetailerWritesDisabled
from open_grocery_mcp.models import StoreInfo
from open_grocery_mcp.workflows import RetailerWorkflowService


class Drafts:
    def get(self, _: str):
        return {
            "basket": {
                "store": "fake",
                "complete": True,
                "details": [
                    {
                        "found": True,
                        "request": {"quantity": 2},
                        "product": {"id": "p1"},
                    }
                ],
            }
        }


class Provider:
    info = StoreInfo(
        key="fake",
        label="Fake",
        country="ES",
        languages=("es",),
        capabilities=("real_cart",),
    )

    def account_status(self):
        return {"authenticated": True}
    def import_browser_session(self, storage_state_path):
        return {"path": storage_state_path}
    def login_with_browser(self, *, timeout_seconds = 300):
        return {"timeout": timeout_seconds}
    def real_cart(self):
        return {"version": 1}
    def preview_cart_update(self, changes, *, mode, expected_version, max_total):
        assert changes == [{"product_id": "p1", "quantity": 2.0}]
        assert max_total == Decimal("10")
        return {
            "estimated_total_text": "4.00",
            "estimated_total": 4,
            "desired_lines": changes,
            "previous_lines": [],
            "expected_cart_version": expected_version,
            "max_total": float(max_total),
        }
    def commit_cart_update(self, plan):
        return {"committed": plan["estimated_total"]}
    def delivery_addresses(self):
        return [{"id": 1}]
    def delivery_slots(self, address_id):
        return [{"id": "slot", "available": True, "open": True}]
    def preview_checkout(self, *, expected_version, max_total):
        return {"cart": {"version": expected_version, "total_text": "4.00"}, "cart_payload": {}}
    def create_checkout(self, plan):
        return {"created": True}
    def get_checkout(self, checkout_id):
        return {
            "checkout_id": checkout_id,
            "total": 4,
            "total_text": "4.00",
            "address_id": 1,
            "slot_id": "slot",
        }
    def set_checkout_delivery(self, checkout_id, *, address_id, slot_id, max_total):
        return {"delivery_updated": True, "checkout_id": checkout_id}
    def submit_order(self, checkout_id, *, max_total):
        return {"order_placed": True}


class Registry:
    def __init__(self): self.provider = Provider()
    def get(self, key):
        assert key == "fake"
        return self.provider


def test_cart_workflow_is_two_phase(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    prepared = service.prepare_cart_update(
        store="fake",
        draft_id="draft",
        max_total=10,
        expected_cart_version=1,
    )
    assert prepared["state_changed"] is False
    result = service.commit_cart_update(
        prepared["confirmation_id"], prepared["confirmation_phrase"]
    )
    assert result == {"committed": 4}


def test_order_workflow_uses_exact_total_phrase(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.setenv("OPEN_GROCERY_ORDER_APPROVAL_CODE", "secret7")
    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    prepared = service.prepare_order_submission(
        store="fake", checkout_id="c1", max_total=5
    )
    assert prepared["confirmation_phrase"] == "COMPRAR 4.00 EUR"
    assert service.submit_order(
        prepared["confirmation_id"], prepared["confirmation_phrase"], "secret7"
    )["order_placed"] is True


def test_retailer_writes_are_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", raising=False)
    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    prepared = service.prepare_cart_update(
        store="fake", draft_id="draft", max_total=10, expected_cart_version=1
    )
    with pytest.raises(RetailerWritesDisabled):
        service.commit_cart_update(
            prepared["confirmation_id"], prepared["confirmation_phrase"]
        )


def test_order_requires_separate_local_approval_code(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.setenv("OPEN_GROCERY_ORDER_APPROVAL_CODE", "secret7")
    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    prepared = service.prepare_order_submission(
        store="fake", checkout_id="c1", max_total=5
    )
    with pytest.raises(OrderApprovalRequired):
        service.submit_order(
            prepared["confirmation_id"], prepared["confirmation_phrase"], "wrong"
        )
