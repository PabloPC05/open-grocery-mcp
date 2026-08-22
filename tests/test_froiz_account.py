"""Fake-level tests for the hybrid Froiz account client safety rules."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    BudgetExceeded,
    ConcurrentCartChange,
    ProviderError,
)
from open_grocery_mcp.providers.froiz_account import FroizAccountClient
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient

PRICES = {"p-old": 2.0, "p-new": 1.5}


def _processed(cart_id: str | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": cart_id or "",
        "items": [
            {
                "comment": item["comment"],
                "enabled": True,
                "product": {
                    "id": item["product_id"],
                    "name": f"N-{item['product_id']}",
                    "price": PRICES.get(item["product_id"], 1.0),
                },
                "qty": item["qty"],
                "unit": item["unit"],
            }
            for item in items
        ],
        "total": round(
            sum(PRICES.get(i["product_id"], 1.0) * i["qty"] for i in items), 2
        ),
    }


class FakeHTTP:
    normalize_cart = staticmethod(FroizHTTPClient.normalize_cart)

    def __init__(self) -> None:
        self.cart_id: str | None = "cart-1"
        self.items: list[dict[str, Any]] = [
            {
                "product_id": "p-old",
                "qty": 1.0,
                "unit": "ud",
                "comment": "",
            }
        ]
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.fail_update = False
        self.fail_channel = False
        self.mutate_response = False

    def channel_cart_id(self) -> str | None:
        if self.fail_channel:
            raise AuthenticationRequired("expired")
        return self.cart_id

    def raw_cart(self, cart_id: str) -> dict[str, Any]:
        assert cart_id == self.cart_id
        return _processed(cart_id, self.items)

    def create_cart(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(("create", deepcopy(items)))
        if self.fail_update:
            raise ProviderError("simulated create failure")
        self.cart_id = "cart-new"
        self.items = deepcopy(items)
        return _processed(self.cart_id, self.items)

    def update_cart(
        self, cart_id: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.calls.append(("update", deepcopy(items)))
        if self.fail_update:
            self.fail_update = False
            raise ProviderError("simulated update failure")
        stored = deepcopy(items)
        if self.mutate_response:
            stored = stored + [
                {"product_id": "p-rogue", "qty": 9.0, "unit": "ud", "comment": ""}
            ]
        self.items = deepcopy(items)
        return _processed(cart_id, stored)

    def delete_cart(self, cart_id: str) -> None:
        self.calls.append(("delete", []))

    def invalidate_session(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeBrowser:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.commit_calls = 0
        self.cart_calls = 0

    def status(self) -> dict[str, Any]:
        return {"store": "froiz", "authenticated_session": True}

    def cart(self) -> dict[str, Any]:
        self.cart_calls += 1
        return {
            "store": "froiz",
            "version": 99,
            "total": 2.0,
            "lines": [],
            "browser_fake": True,
        }

    def commit_cart_update(self, plan):
        self.commit_calls += 1
        return {"browser_commit": True}

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        return {"login_opened": True, "timeout_seconds": timeout_seconds}

    def import_storage_state(self, storage_state_path: str) -> dict[str, Any]:
        return {"imported": storage_state_path}

    def close(self) -> None:
        pass


@pytest.fixture
def account(tmp_path: Path):
    fake = FakeHTTP()
    browser = FakeBrowser(tmp_path / "storage_state.json")
    client = FroizAccountClient(browser=browser, http=fake)
    return client, fake, browser


def test_real_cart_uses_http_backend(account) -> None:
    client, _, browser = account
    result = client.real_cart()
    assert result["cart_backend"] == "froiz_http"
    assert result["browser_driven"] is False
    assert result["products_count"] == 1
    assert browser.cart_calls == 0


def test_real_cart_falls_back_on_auth_failure(account) -> None:
    client, fake, browser = account
    fake.fail_channel = True
    result = client.real_cart()
    assert result["cart_backend"] == "browser"
    assert result["http_fallback_reason"] == "AuthenticationRequired"


def test_preview_merges_changes_and_checks_version(account) -> None:
    client, _, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2, "unit_price": 1.5}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    ids = sorted(i["product_id"] for i in plan["desired_items"])
    assert ids == ["p-new", "p-old"]
    assert plan["estimated_total_text"] == "5.00"


def test_preview_replace_drops_existing_lines(account) -> None:
    client, _, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1}],
        mode="replace",
        expected_version=version,
        max_total=Decimal("10"),
    )
    assert [i["product_id"] for i in plan["desired_items"]] == ["p-new"]


def test_preview_rejects_stale_version(account) -> None:
    client, fake, _ = account
    with pytest.raises(ConcurrentCartChange):
        client.preview_cart_update(
            [{"product_id": "p-new", "quantity": 1}],
            mode="merge",
            expected_version=12345,
            max_total=Decimal("10"),
        )
    assert fake.calls == []


def test_preview_enforces_budget(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    with pytest.raises(BudgetExceeded):
        client.preview_cart_update(
            [
                {
                    "product_id": "p-new",
                    "quantity": 50,
                    "unit_price": 1.5,
                }
            ],
            mode="merge",
            expected_version=version,
            max_total=Decimal("5"),
        )
    assert fake.calls == []


def test_commit_updates_existing_cart_over_http(account) -> None:
    client, fake, browser = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    result = client.commit_cart_update(plan)
    assert ("update", plan["desired_items"]) in fake.calls
    assert browser.commit_calls == 0
    assert result["cart_backend"] == "froiz_http"
    assert result["order_placed"] is False


def test_commit_creates_when_no_cart_is_bound(account) -> None:
    client, fake, _ = account
    fake.cart_id = None
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1}],
        mode="merge",
        expected_version=None,
        max_total=Decimal("10"),
    )
    result = client.commit_cart_update(plan)
    assert [op for op, _ in fake.calls] == ["create"]
    assert result["cart_id"] == "cart-new"


def test_commit_detects_concurrent_change_before_writing(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    # Concurrent modification after review.
    fake.items.append(
        {"product_id": "p-other", "qty": 3.0, "unit": "ud", "comment": ""}
    )
    with pytest.raises(ConcurrentCartChange):
        client.commit_cart_update(plan)
    assert fake.calls == []


def test_commit_rolls_back_to_previous_items_on_failure(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    fake.fail_update = True
    with pytest.raises(ProviderError, match="previous cart restored"):
        client.commit_cart_update(plan)
    updates = [items for op, items in fake.calls if op == "update"]
    assert updates and updates[-1] == plan["previous_items"]


def test_commit_rolls_back_when_result_does_not_match_review(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    fake.mutate_response = True
    with pytest.raises(ProviderError, match="previous cart restored"):
        client.commit_cart_update(plan)
    updates = [items for op, items in fake.calls if op == "update"]
    assert updates[-1] == plan["previous_items"]


def test_account_never_touches_order_or_payment_endpoints(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    client.commit_cart_update(plan)
    recorded = json.dumps(fake.calls, default=str)
    assert "/orders" not in recorded
    assert "/api/payment" not in recorded


