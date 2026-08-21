import json
from decimal import Decimal
from pathlib import Path

import pytest

from open_grocery_mcp.errors import (
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    ProviderError,
)
from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import BrowserStoreConfig
from open_grocery_mcp.providers.browser_normalize import cart_version


CONFIG = BrowserStoreConfig(key="demo", label="Demo", base_url="https://demo.test", cart_paths=("/cart",))


class FakeDriver:
    shared = None

    def __init__(self, config, *, state_path, checkout_store, timeout_seconds=30):
        self.config = config
        self.state_path = Path(state_path)
        if FakeDriver.shared is None:
            FakeDriver.shared = {
                "lines": [{"product_id": "old", "name": "Pan", "quantity": 1.0, "unit_price": 1.0, "url": "https://demo.test/product/pan"}],
                "addresses": [{"id": "a1", "label": "28050 · Madrid", "street_redacted": True, "default": True}],
                "slots": [{"id": "s1", "label": "18:00-20:00", "available": True, "open": True, "price": 4.0, "price_text": "4.00"}],
                "rollback_count": 0,
                "overcharge": False,
                "submit_count": 0,
                "submit_raises": False,
            }

    @classmethod
    def reset(cls):
        cls.shared = None

    def login(self, *, timeout_seconds):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"cookies": [{"domain": "demo.test", "name": "s", "value": "x"}], "origins": []}))
        return {"session_saved": True}

    def read_cart(self):
        lines = [dict(x) for x in self.shared["lines"]]
        total = sum(Decimal(str(x["unit_price"])) * Decimal(str(x["quantity"])) for x in lines)
        return {"store": "demo", "cart_id": "c", "version": cart_version(lines, total), "products_count": len(lines), "total": float(total), "total_text": f"{total:.2f}", "currency": "EUR", "lines": lines}

    def apply_cart(self, desired):
        if self.shared["lines"] and desired and self.shared["lines"][0].get("product_id") == "rollback-marker":
            self.shared["rollback_count"] += 1
        self.shared["lines"] = [dict(x) for x in desired]
        cart = self.read_cart()
        if self.shared["overcharge"]:
            cart["total"] = 999.0
            cart["total_text"] = "999.00"
            self.shared["overcharge"] = False
        return cart

    def addresses(self):
        return list(self.shared["addresses"])
    def slots(self, address_id, checkout_url=None):
        return list(self.shared["slots"])
    def create_checkout(self):
        cart = self.read_cart()
        return {"url": "https://demo.test/checkout/1", "_private_url": "https://demo.test/checkout/1?token=secret", "cart": cart, "total": cart["total"], "total_text": cart["total_text"], "address_id": None, "slot_id": None, "state_changed": True, "order_placed": False}
    def checkout(self, url):
        cart = self.read_cart()
        return {"url": url, "total": cart["total"], "total_text": cart["total_text"], "currency": "EUR", "cart_version": cart["version"], "address_id": None, "slot_id": None, "order_placed": False}
    def set_delivery(self, url, *, address_id, slot_id):
        cart = self.read_cart()
        return {"url": "https://demo.test/checkout/1", "_private_url": url, "total": cart["total"] + 4, "total_text": f"{cart['total'] + 4:.2f}", "currency": "EUR", "cart_version": cart["version"], "address_id": str(address_id), "slot_id": slot_id, "order_placed": False, "state_changed": True}
    def submit(self, url):
        self.shared["submit_count"] += 1
        if self.shared["submit_raises"]:
            raise RuntimeError("ambiguous browser failure after click")
        return {
            "store": "demo",
            "order_placed": True,
            "submission_attempted": True,
            "order_id": "o1",
            "requires_user_action": False,
            "status": "confirmed",
            "total": self.read_cart()["total"],
            "total_text": self.read_cart()["total_text"],
            "page_url": url,
        }


@pytest.fixture
def account(tmp_path):
    FakeDriver.reset()
    client = BrowserAccountClient(CONFIG, state_root=tmp_path, driver_factory=FakeDriver)
    client.login_with_browser(timeout_seconds=60)
    return client


def test_preview_and_commit_replace_cart(account):
    cart = account.cart()
    plan = account.preview_cart_update([
        {"product_id": "milk", "name": "Leche", "url": "https://demo.test/product/milk", "quantity": 2, "unit_price": 1.25}
    ], mode="replace", expected_version=cart["version"], max_total=Decimal("5"))
    assert plan["estimated_total_text"] == "2.50"
    updated = account.commit_cart_update(plan)
    assert updated["verified_against_reviewed_plan"] is True
    assert updated["lines"][0]["quantity"] == 2.0


def test_concurrent_change_is_rejected(account):
    cart = account.cart()
    plan = account.preview_cart_update([
        {"product_id": "milk", "name": "Leche", "url": "https://demo.test/product/milk", "quantity": 1, "unit_price": 1.25}
    ], mode="replace", expected_version=cart["version"], max_total=Decimal("5"))
    FakeDriver.shared["lines"].append({"product_id": "x", "name": "X", "quantity": 1, "unit_price": 1, "url": "https://demo.test/product/x"})
    with pytest.raises(ConcurrentCartChange):
        account.commit_cart_update(plan)


def test_restricted_products_and_budget_are_rejected(account):
    cart = account.cart()
    with pytest.raises(InvalidRequest, match="age-restricted"):
        account.preview_cart_update([
            {"product_id": "wine", "name": "Vino tinto", "url": "https://demo.test/product/wine", "quantity": 1, "unit_price": 4}
        ], mode="replace", expected_version=cart["version"], max_total=Decimal("10"))
    with pytest.raises(BudgetExceeded):
        account.preview_cart_update([
            {"product_id": "milk", "name": "Leche", "url": "https://demo.test/product/milk", "quantity": 10, "unit_price": 2}
        ], mode="replace", expected_version=cart["version"], max_total=Decimal("5"))


def test_checkout_delivery_and_submission(account, monkeypatch):
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")
    cart = account.cart()
    plan = account.preview_checkout(expected_version=cart["version"], max_total=Decimal("10"))
    checkout = account.create_checkout(plan)
    assert checkout["checkout_id"].startswith("demo-")
    delivered = account.set_checkout_delivery(checkout["checkout_id"], address_id="a1", slot_id="s1", max_total=Decimal("10"))
    assert delivered["slot_id"] == "s1"
    result = account.submit_order(checkout["checkout_id"], max_total=Decimal("10"))
    assert result["order_placed"] is True


def test_import_rejects_unrelated_storage_state(tmp_path):
    source = tmp_path / "unrelated.json"
    source.write_text(json.dumps({"cookies": [{"domain": "example.com", "name": "s", "value": "x"}], "origins": []}))
    client = BrowserAccountClient(CONFIG, state_root=tmp_path / "state", driver_factory=FakeDriver)
    with pytest.raises(InvalidRequest, match="Demo cookies"):
        client.import_storage_state(str(source))


def test_checkout_private_url_is_not_exposed(account):
    cart = account.cart()
    plan = account.preview_checkout(expected_version=cart["version"], max_total=Decimal("10"))
    checkout = account.create_checkout(plan)
    assert "_private_url" not in checkout
    stored = account._checkout_record(checkout["checkout_id"])
    assert stored["url"].endswith("?token=secret")


def test_commit_rejects_unverifiable_total_and_rolls_back(account):
    cart = account.cart()
    plan = account.preview_cart_update([
        {"product_id": "milk", "name": "Leche", "url": "https://demo.test/product/milk", "quantity": 1, "unit_price": 1.25}
    ], mode="replace", expected_version=cart["version"], max_total=Decimal("5"))
    original_apply = FakeDriver.apply_cart

    def zero_total(self, desired):
        result = original_apply(self, desired)
        if desired and desired[0].get("product_id") == "milk":
            result["total"] = 0
            result["total_text"] = "0.00"
        return result

    FakeDriver.apply_cart = zero_total
    try:
        with pytest.raises(BudgetExceeded, match="positive Demo cart total"):
            account.commit_cart_update(plan)
    finally:
        FakeDriver.apply_cart = original_apply


def test_delivery_slots_require_a_confirmed_checkout(account):
    with pytest.raises(InvalidRequest, match="create a confirmed Demo checkout"):
        account.slots("a1")


def test_order_submission_refuses_automatic_retry(account, monkeypatch):
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")
    cart = account.cart()
    plan = account.preview_checkout(expected_version=cart["version"], max_total=Decimal("10"))
    checkout = account.create_checkout(plan)
    account.set_checkout_delivery(
        checkout["checkout_id"],
        address_id="a1",
        slot_id="s1",
        max_total=Decimal("10"),
    )
    first = account.submit_order(checkout["checkout_id"], max_total=Decimal("10"))
    assert first["order_placed"] is True
    with pytest.raises(InvalidRequest, match="already attempted"):
        account.submit_order(checkout["checkout_id"], max_total=Decimal("10"))
    assert FakeDriver.shared["submit_count"] == 1


def test_ambiguous_submission_is_recorded_and_not_retried(account, monkeypatch):
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")
    cart = account.cart()
    plan = account.preview_checkout(
        expected_version=cart["version"],
        max_total=Decimal("10"),
    )
    checkout = account.create_checkout(plan)
    account.set_checkout_delivery(
        checkout["checkout_id"],
        address_id="a1",
        slot_id="s1",
        max_total=Decimal("10"),
    )
    FakeDriver.shared["submit_raises"] = True
    with pytest.raises(ProviderError, match="could not be verified"):
        account.submit_order(checkout["checkout_id"], max_total=Decimal("10"))
    with pytest.raises(InvalidRequest, match="already attempted"):
        account.submit_order(checkout["checkout_id"], max_total=Decimal("10"))
    assert FakeDriver.shared["submit_count"] == 1


def test_quantity_safety_limit(account):
    cart = account.cart()
    with pytest.raises(InvalidRequest, match="safety limit of 1000"):
        account.preview_cart_update(
            [
                {
                    "product_id": "water",
                    "name": "Agua mineral 1 litro",
                    "url": "https://demo.test/product/water",
                    "quantity": 1001,
                    "unit_price": 1,
                }
            ],
            mode="replace",
            expected_version=cart["version"],
            max_total=Decimal("2000"),
        )


def test_default_state_root_matches_canonical_session_dir(monkeypatch):
    from open_grocery_mcp.providers.browser_account_state import default_state_root

    monkeypatch.delenv("OPEN_GROCERY_STATE_DIR", raising=False)
    assert default_state_root() == Path.home() / ".open-grocery-mcp"


def test_default_state_root_respects_override(monkeypatch, tmp_path):
    from open_grocery_mcp.providers.browser_account_state import default_state_root

    monkeypatch.setenv("OPEN_GROCERY_STATE_DIR", str(tmp_path))
    assert default_state_root() == tmp_path
