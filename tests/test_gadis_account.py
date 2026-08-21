from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from open_grocery_mcp.errors import (
    BudgetExceeded,
    ConcurrentCartChange,
    ProviderError,
)
from open_grocery_mcp.providers.gadis_account import GadisAccountClient
from open_grocery_mcp.providers.gadis_http import GadisHTTPClient


class FakeBrowser:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.preview_calls = 0
        self.commit_calls = 0

    def status(self) -> dict[str, Any]:
        return {"store": "gadis", "authenticated_session": True}

    def import_storage_state(self, storage_state_path: str) -> dict[str, Any]:
        return {"imported": storage_state_path}

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        return {"login_opened": True, "timeout_seconds": timeout_seconds}

    def cart(self) -> dict[str, Any]:
        return {
            "store": "gadis",
            "version": 99,
            "total": 2.0,
            "lines": [],
            "browser_fake": True,
        }

    def preview_cart_update(self, changes, *, mode, expected_version, max_total):
        self.preview_calls += 1
        return {
            "store": "gadis",
            "estimated_total": 2.0,
            "estimated_total_text": "2.00",
            "desired_lines": list(changes),
            "previous_lines": [],
            "max_total": float(max_total),
            "browser_preview": True,
        }

    def commit_cart_update(self, plan):
        self.commit_calls += 1
        return {"browser_commit": True, "plan": plan}

    def addresses(self):
        return [{"id": "browser-address"}]

    def slots(self, address_id):
        return [{"id": "browser-slot", "address_id": str(address_id)}]

    def preview_checkout(self, *, expected_version, max_total):
        return {"browser_checkout_preview": True}

    def create_checkout(self, plan):
        return {"browser_checkout_created": True}

    def get_checkout(self, checkout_id):
        return {"checkout_id": checkout_id}

    def set_checkout_delivery(self, checkout_id, *, address_id, slot_id, max_total):
        return {
            "checkout_id": checkout_id,
            "address_id": str(address_id),
            "slot_id": slot_id,
        }

    def submit_order(self, checkout_id, *, max_total):
        return {"checkout_id": checkout_id, "order_placed": False}

    def close(self) -> None:
        pass


class FakeHTTP:
    normalize_cart = staticmethod(GadisHTTPClient.normalize_cart)

    def __init__(self) -> None:
        self.raw = {
            "id": "cart-1",
            "store_id": "store-7",
            "products": [
                {
                    "product_id": "p-old",
                    "product_name": "Arroz",
                    "amount": 1,
                    "line_price": 2.0,
                }
            ],
            "total_cart_price": 2.0,
            "total_products": 1,
            "last_modified_date": 10,
        }
        self.prices = {"p-old": 2.0, "p-new": 1.5}
        self.actual_prices: dict[str, float] = {}
        self.update_calls: list[tuple[str, int]] = []
        self.raise_after_apply = False
        self.invalidated = 0

    def _recalculate(self) -> None:
        total = 0.0
        for line in self.raw["products"]:
            total += float(line["line_price"]) * float(line["amount"])
        self.raw["total_cart_price"] = round(total, 2)
        self.raw["total_products"] = len(self.raw["products"])

    def invalidate_session(self) -> None:
        self.invalidated += 1

    def status(self) -> dict[str, Any]:
        return {
            "store": "gadis",
            "authenticated": True,
            "http_session_checked": True,
        }

    def read_cart(self) -> dict[str, Any]:
        # The retailer bumps last_modified_date on every cart fetch, so the
        # raw timestamp must never be usable as an optimistic-lock version.
        self.raw["last_modified_date"] += 2312
        return deepcopy(self.raw)

    def update_product(
        self,
        cart_id: str,
        store_id: str,
        product_id: str,
        amount: int,
        **_: Any,
    ) -> dict[str, Any]:
        assert cart_id == "cart-1"
        assert store_id == "store-7"
        self.update_calls.append((product_id, amount))
        rows = self.raw["products"]
        existing = next((line for line in rows if line["product_id"] == product_id), None)
        if amount <= 0:
            if existing is not None:
                rows.remove(existing)
        elif existing is None:
            rows.append(
                {
                    "product_id": product_id,
                    "product_name": product_id,
                    "amount": amount,
                    "line_price": self.actual_prices.get(
                        product_id, self.prices[product_id]
                    ),
                }
            )
        else:
            existing["amount"] = amount
            existing["line_price"] = self.actual_prices.get(
                product_id, self.prices[product_id]
            )
        self.raw["last_modified_date"] += 1
        self._recalculate()
        if self.raise_after_apply:
            self.raise_after_apply = False
            raise ProviderError("simulated lost response")
        return self.normalize_cart(self.raw)

    def close(self) -> None:
        pass


def _account(tmp_path: Path) -> tuple[GadisAccountClient, FakeHTTP, FakeBrowser]:
    http = FakeHTTP()
    browser = FakeBrowser(tmp_path / "storage_state.json")
    return GadisAccountClient(browser=browser, http=http), http, browser


def test_gadis_account_uses_http_for_reviewed_cart_update(tmp_path: Path) -> None:
    account, http, browser = _account(tmp_path)
    plan = account.preview_cart_update(
        [
            {
                "product_id": "p-new",
                "name": "Leche",
                "quantity": 2,
                "unit_price": 1.5,
            }
        ],
        mode="merge",
        expected_version=account.cart()["version"],
        max_total=Decimal("6"),
    )
    assert plan["plan_backend"] == "gadis_http"
    assert plan["estimated_total_text"] == "5.00"

    result = account.commit_cart_update(plan)

    assert result["cart_backend"] == "gadis_http"
    assert result["verified_against_reviewed_plan"] is True
    assert [(line["product_id"], line["quantity"]) for line in result["lines"]] == [
        ("p-old", 1.0),
        ("p-new", 2.0),
    ]
    assert http.update_calls == [("p-new", 2)]
    assert browser.preview_calls == 0
    assert browser.commit_calls == 0


def test_gadis_replace_removes_before_adding(tmp_path: Path) -> None:
    account, http, _ = _account(tmp_path)
    plan = account.preview_cart_update(
        [
            {
                "product_id": "p-new",
                "name": "Leche",
                "quantity": 1,
                "unit_price": 1.5,
            }
        ],
        mode="replace",
        expected_version=account.cart()["version"],
        max_total=Decimal("3"),
    )
    account.commit_cart_update(plan)
    assert http.update_calls == [("p-old", 0), ("p-new", 1)]


def test_fractional_gadis_quantity_uses_browser_fallback(tmp_path: Path) -> None:
    account, _, browser = _account(tmp_path)
    plan = account.preview_cart_update(
        [
            {
                "product_id": "fresh",
                "name": "Tomate",
                "quantity": 0.5,
                "unit_price": 3,
            }
        ],
        mode="merge",
        expected_version=None,
        max_total=Decimal("10"),
    )
    assert plan["plan_backend"] == "browser"
    assert plan["browser_preview"] is True
    account.commit_cart_update(plan)
    assert browser.preview_calls == 1
    assert browser.commit_calls == 1


def test_gadis_http_cart_rolls_back_when_actual_total_exceeds_cap(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    plan = account.preview_cart_update(
        [
            {
                "product_id": "p-new",
                "name": "Leche",
                "quantity": 1,
                "unit_price": 1.5,
            }
        ],
        mode="merge",
        expected_version=account.cart()["version"],
        max_total=Decimal("4"),
    )
    http.actual_prices["p-new"] = 9.0

    with pytest.raises(BudgetExceeded, match="previous Gadis cart restored"):
        account.commit_cart_update(plan)

    assert [(line["product_id"], line["amount"]) for line in http.raw["products"]] == [
        ("p-old", 1)
    ]
    assert http.update_calls == [("p-new", 1), ("p-new", 0)]


def test_ambiguous_write_response_is_accepted_only_after_safe_read(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    plan = account.preview_cart_update(
        [
            {
                "product_id": "p-new",
                "name": "Leche",
                "quantity": 1,
                "unit_price": 1.5,
            }
        ],
        mode="merge",
        expected_version=account.cart()["version"],
        max_total=Decimal("5"),
    )
    http.raise_after_apply = True

    result = account.commit_cart_update(plan)

    assert result["write_response_ambiguous_but_state_verified"] is True
    assert http.update_calls == [("p-new", 1)]


def test_concurrent_content_change_between_review_and_commit_is_rejected(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    plan = account.preview_cart_update(
        [
            {
                "product_id": "p-new",
                "name": "Leche",
                "quantity": 1,
                "unit_price": 1.5,
            }
        ],
        mode="merge",
        expected_version=account.cart()["version"],
        max_total=Decimal("5"),
    )
    # A concurrent edit changes the reviewed cart content before the commit.
    http.raw["products"][0]["amount"] = 4
    http._recalculate()

    with pytest.raises(ConcurrentCartChange, match="changed from version"):
        account.commit_cart_update(plan)

    assert http.update_calls == []


def test_gadis_delivery_and_checkout_remain_browser_backed(tmp_path: Path) -> None:
    account, _, _ = _account(tmp_path)
    addresses = account.addresses()
    assert addresses == [{"id": "browser-address"}]
    assert account.slots("browser-address") == [
        {"id": "browser-slot", "address_id": "browser-address"}
    ]
    delivery = account.set_checkout_delivery(
        "checkout-1",
        address_id="browser-address",
        slot_id="browser-slot",
        max_total=Decimal("20"),
    )
    assert delivery["address_id"] == "browser-address"


def test_session_replacement_invalidates_cached_http_state(tmp_path: Path) -> None:
    account, http, _ = _account(tmp_path)
    account.import_storage_state("new-state.json")
    account.login_with_browser(timeout_seconds=12)
    assert http.invalidated == 2
    status = account.status()
    assert status["validated_live"] is True
    assert status["cart_backend"] == "gadis_http_with_browser_fallback"
