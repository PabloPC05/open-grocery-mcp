from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from open_grocery_mcp.errors import ConcurrentCartChange
from open_grocery_mcp.providers.gadis_account import GadisAccountClient
from open_grocery_mcp.providers.gadis_http import GadisHTTPClient


class CheckoutHTTP:
    normalize_cart = staticmethod(GadisHTTPClient.normalize_cart)

    def __init__(self) -> None:
        self.raw = {
            "id": "cart-http",
            "store_id": "store-7",
            "products": [
                {
                    "product_id": "p-rice",
                    "product_name": "Arroz redondo 1 kg",
                    "amount": 1,
                    "line_price": 2.0,
                }
            ],
            "total_cart_price": 2.0,
            "total_products": 1,
            "last_modified_date": 10,
        }

    def read_cart(self) -> dict[str, Any]:
        # The retailer bumps last_modified_date on every cart fetch.
        self.raw["last_modified_date"] += 2312
        return deepcopy(self.raw)

    def status(self) -> dict[str, Any]:
        return {
            "store": "gadis",
            "authenticated": True,
            "http_session_checked": True,
        }

    def close(self) -> None:
        pass


class CheckoutBrowser:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.cart_payload: dict[str, Any] = {
            "store": "gadis",
            "cart_id": "browser-cart",
            "version": 99,
            "total": 2.0,
            "total_text": "2.00",
            "currency": "EUR",
            "lines": [
                {
                    "product_id": "p-rice",
                    "name": "Arroz redondo 1 kg",
                    "quantity": 1.0,
                    "unit_price": 2.0,
                }
            ],
        }
        self.received_plan: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {"store": "gadis", "authenticated_session": True}

    def cart(self) -> dict[str, Any]:
        return deepcopy(self.cart_payload)

    def create_checkout(self, plan) -> dict[str, Any]:
        self.received_plan = dict(plan)
        assert self.received_plan["expected_cart_version"] == 99
        return {
            "store": "gadis",
            "checkout_id": "checkout-browser",
            "total": 2.0,
            "total_text": "2.00",
            "order_placed": False,
        }

    def close(self) -> None:
        pass


def _account(tmp_path: Path) -> tuple[GadisAccountClient, CheckoutHTTP, CheckoutBrowser]:
    http = CheckoutHTTP()
    browser = CheckoutBrowser(tmp_path / "storage_state.json")
    return GadisAccountClient(browser=browser, http=http), http, browser


def test_checkout_translates_http_version_after_cross_backend_verification(
    tmp_path: Path,
) -> None:
    account, _, browser = _account(tmp_path)
    reviewed_version = account.cart()["version"]

    plan = account.preview_checkout(
        expected_version=reviewed_version,
        max_total=Decimal("5"),
    )

    assert plan["expected_cart_version"] == reviewed_version
    assert plan["reviewed_cart_backend"] == "gadis_http"
    result = account.create_checkout(plan)

    assert browser.received_plan is not None
    assert browser.received_plan["reviewed_http_cart_version"] == reviewed_version
    assert result["checkout_backend"] == "browser"
    assert result["reviewed_cart_backend"] == "gadis_http"
    assert result["order_placed"] is False


def test_checkout_refuses_browser_cart_that_differs_from_reviewed_http_cart(
    tmp_path: Path,
) -> None:
    account, _, browser = _account(tmp_path)
    plan = account.preview_checkout(
        expected_version=account.cart()["version"],
        max_total=Decimal("5"),
    )
    browser.cart_payload["lines"][0]["quantity"] = 2.0
    browser.cart_payload["total"] = 4.0

    with pytest.raises(ConcurrentCartChange, match="does not match"):
        account.create_checkout(plan)


def test_checkout_refuses_same_lines_with_different_total(tmp_path: Path) -> None:
    account, _, browser = _account(tmp_path)
    plan = account.preview_checkout(
        expected_version=account.cart()["version"],
        max_total=Decimal("5"),
    )
    browser.cart_payload["total"] = 2.5

    with pytest.raises(ConcurrentCartChange, match="total does not match"):
        account.create_checkout(plan)
