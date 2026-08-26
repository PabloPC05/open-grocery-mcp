from __future__ import annotations

from decimal import Decimal

import pytest

from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.errors import (
    InvalidRequest,
    OrderApprovalRequired,
    OrderSubmissionDisabled,
    RetailerWritesDisabled,
    UnsupportedOperation,
)
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
    capabilities=("real_cart", "delivery", "checkout"),
    )

    def __init__(self):
        self._last_plan = None
        self.checkout_total = 4
        self.submit_calls = 0
        self.open_calls = 0

    def account_status(self):
        return {"authenticated": True}
    def import_browser_session(self, storage_state_path):
        return {"path": storage_state_path}
    def login_with_browser(self, *, timeout_seconds = 300):
        return {"timeout": timeout_seconds}
    def real_cart(self):
        return {
            "version": 1,
            "total": 4,
            "total_text": "4.00",
            "lines": [{"product_id": "p1", "quantity": 2}],
        }
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
        self._last_plan = plan
        return {"created": True}
    def get_checkout(self, checkout_id):
        return {
            "checkout_id": checkout_id,
            "total": self.checkout_total,
            "total_text": f"{self.checkout_total:.2f}",
            "address_id": 1,
            "slot_id": "slot",
        }
    def set_checkout_delivery(self, checkout_id, *, address_id, slot_id, max_total):
        return {"delivery_updated": True, "checkout_id": checkout_id}
    def submit_order(self, checkout_id, *, max_total):
        self.submit_calls += 1
        return {"order_placed": True}
    def open_human_review(self, **kwargs):
        self.open_calls += 1
        return {
            "window_opened": True,
            "automated_clicks": 0,
            "automated_order_submission": False,
            **kwargs,
        }


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


def test_checkout_creation_embeds_reviewed_delivery(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    registry = Registry()
    service = RetailerWorkflowService(registry, Drafts(), ConfirmationStore())
    prepared = service.prepare_checkout_creation(
        store="fake",
        max_total=10,
        expected_cart_version=1,
        shipping_address_id="addr-1",
        delivery_date="2026-08-25",
        schedule_range_id="slot",
    )
    assert prepared["confirmation_phrase"] == "CREAR CHECKOUT 4.00 EUR"
    committed = service.commit_checkout_creation(
        prepared["confirmation_id"], prepared["confirmation_phrase"]
    )
    assert committed == {"created": True}
    assert registry.provider._last_plan["delivery"] == {
        "shipping_address_id": "addr-1",
        "delivery_date": "2026-08-25",
        "schedule_range_id": "slot",
    }


def test_partial_delivery_triple_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    from open_grocery_mcp.errors import InvalidRequest

    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    with pytest.raises(InvalidRequest):
        service.prepare_checkout_creation(
            store="fake",
            max_total=10,
            expected_cart_version=1,
            shipping_address_id="addr-1",
        )


def test_order_workflow_uses_exact_total_phrase(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")
    monkeypatch.setenv("OPEN_GROCERY_ORDER_APPROVAL_CODE", "secret7")
    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    prepared = service.prepare_order_submission(
        store="fake", checkout_id="c1", max_total=5
    )
    assert prepared["confirmation_phrase"] == "COMPRAR 4.00 EUR"
    assert service.submit_order(
        prepared["confirmation_id"], prepared["confirmation_phrase"], "secret7"
    )["order_placed"] is True


def test_order_confirmation_is_bound_to_the_exact_reviewed_total(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")
    monkeypatch.setenv("OPEN_GROCERY_ORDER_APPROVAL_CODE", "secret7")
    registry = Registry()
    service = RetailerWorkflowService(registry, Drafts(), ConfirmationStore())
    prepared = service.prepare_order_submission(
        store="fake", checkout_id="c1", max_total=5
    )
    registry.provider.checkout_total = 3

    with pytest.raises(InvalidRequest, match="changed after explicit confirmation"):
        service.submit_order(
            prepared["confirmation_id"],
            prepared["confirmation_phrase"],
            "secret7",
        )

    assert registry.provider.submit_calls == 0


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
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")
    monkeypatch.setenv("OPEN_GROCERY_ORDER_APPROVAL_CODE", "secret7")
    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    prepared = service.prepare_order_submission(
        store="fake", checkout_id="c1", max_total=5
    )
    with pytest.raises(OrderApprovalRequired):
        service.submit_order(
            prepared["confirmation_id"], prepared["confirmation_phrase"], "wrong"
        )


def test_order_workflow_requires_explicit_submission_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.setenv("OPEN_GROCERY_ORDER_APPROVAL_CODE", "secret7")
    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    prepared = service.prepare_order_submission(
        store="fake", checkout_id="c1", max_total=5
    )
    with pytest.raises(OrderSubmissionDisabled):
        service.submit_order(
            prepared["confirmation_id"], prepared["confirmation_phrase"], "secret7"
        )


def test_delivery_selection_rejects_invalid_cap_before_confirmation() -> None:
    service = RetailerWorkflowService(Registry(), Drafts(), ConfirmationStore())
    with pytest.raises(InvalidRequest, match="greater than zero"):
        service.prepare_delivery_selection(
            store="fake",
            checkout_id="c1",
            address_id=1,
            slot_id="slot",
            max_total=0,
        )


def test_workflow_respects_declared_capabilities() -> None:
    registry = Registry()
    registry.provider.info = StoreInfo(
        key="fake",
        label="Fake",
        country="ES",
        languages=("es",),
        capabilities=("real_cart",),
    )
    service = RetailerWorkflowService(registry, Drafts(), ConfirmationStore())
    with pytest.raises(UnsupportedOperation, match="no checkout support"):
        service.get_checkout("fake", "c1")

    registry.provider.info = StoreInfo(
        key="fake",
        label="Fake",
        country="ES",
        languages=("es",),
        capabilities=(),
    )
    with pytest.raises(UnsupportedOperation, match="no authenticated cart support"):
        service.real_cart("fake")


def test_checkout_handoff_revalidates_total_delivery_and_never_submits() -> None:
    registry = Registry()
    registry.provider.info = StoreInfo(
        key="fake",
        label="Fake",
        country="ES",
        languages=("es",),
        capabilities=("real_cart", "delivery", "checkout", "human_handoff"),
    )
    service = RetailerWorkflowService(registry, Drafts(), ConfirmationStore())

    handoff = service.prepare_human_handoff(
        store="fake",
        checkout_id="c1",
        max_total=5,
    )

    assert handoff["handoff_stage"] == "checkout_review"
    assert handoff["ready_for_human_review"] is True
    assert handoff["delivery_verified"] is True
    assert handoff["automated_order_submission"] is False
    assert registry.provider.submit_calls == 0


def test_cart_only_handoff_stops_before_unsafe_checkout() -> None:
    registry = Registry()
    registry.provider.info = StoreInfo(
        key="fake",
        label="Fake",
        country="ES",
        languages=("es",),
        capabilities=("real_cart", "delivery", "human_handoff"),
    )
    service = RetailerWorkflowService(registry, Drafts(), ConfirmationStore())

    handoff = service.prepare_human_handoff(
        store="fake",
        max_total=5,
        address_id=1,
        slot_id="slot",
    )

    assert handoff["handoff_stage"] == "verified_cart"
    assert handoff["safe_checkout_boundary_available"] is False
    assert handoff["delivery_verified"] is True
    assert registry.provider.submit_calls == 0


def test_visible_handoff_validates_before_opening_and_performs_no_clicks() -> None:
    registry = Registry()
    registry.provider.info = StoreInfo(
        key="fake",
        label="Fake",
        country="ES",
        languages=("es",),
        capabilities=("real_cart", "delivery", "checkout", "human_handoff"),
    )
    service = RetailerWorkflowService(registry, Drafts(), ConfirmationStore())

    result = service.open_human_review(
        store="fake",
        checkout_id="c1",
        max_total=5,
        timeout_seconds=30,
    )

    assert result["window"]["window_opened"] is True
    assert result["window"]["automated_clicks"] == 0
    assert result["automated_order_submission"] is False
    assert registry.provider.open_calls == 1
    assert registry.provider.submit_calls == 0


def test_handoff_refuses_changed_total_before_opening() -> None:
    registry = Registry()
    registry.provider.info = StoreInfo(
        key="fake",
        label="Fake",
        country="ES",
        languages=("es",),
        capabilities=("real_cart", "delivery", "checkout", "human_handoff"),
    )
    registry.provider.checkout_total = 6
    service = RetailerWorkflowService(registry, Drafts(), ConfirmationStore())

    with pytest.raises(InvalidRequest, match="checkout total"):
        service.open_human_review(
            store="fake",
            checkout_id="c1",
            max_total=5,
            timeout_seconds=30,
        )

    assert registry.provider.open_calls == 0
    assert registry.provider.submit_calls == 0
