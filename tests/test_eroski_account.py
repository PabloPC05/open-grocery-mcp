from __future__ import annotations

from decimal import Decimal

import pytest

from open_grocery_mcp.errors import ConcurrentCartChange, ProviderError
from open_grocery_mcp.providers.eroski_full import EroskiFullProvider
from open_grocery_mcp.providers.eroski_http import EroskiCart, EroskiCartItem


class FakeHTTP:
    def __init__(self) -> None:
        self.fail = False
        self.cart = EroskiCart(items=[EroskiCartItem("old", 1)], total_text="1,00€")

    def read_cart(self) -> EroskiCart:
        if self.fail:
            raise ProviderError("offline")
        return self.cart


class FakeBrowserAccount:
    def __init__(self) -> None:
        self.preview_expected_versions: list[int | None] = []
        self.commits = 0

    def cart(self):
        return {
            "version": 77,
            "total": 1.0,
            "lines": [{"product_id": "old", "quantity": 1}],
        }

    def preview_cart_update(self, changes, *, mode, expected_version, max_total):
        self.preview_expected_versions.append(expected_version)
        return {
            "changes": changes,
            "mode": mode,
            "max_total": float(max_total),
            "expected_cart_version": 77,
        }

    def commit_cart_update(self, plan):
        self.commits += 1
        return {"committed": True, "plan": plan}


class FakeDelivery:
    def delivery_addresses(self):
        return [{"id": "address-1", "street_redacted": True}]

    def delivery_slots(self, address_id):
        assert address_id == "address-1"
        return [{"id": "slot-1", "available": True}]


def _provider() -> tuple[EroskiFullProvider, FakeHTTP, FakeBrowserAccount]:
    provider = object.__new__(EroskiFullProvider)
    http = FakeHTTP()
    browser = FakeBrowserAccount()
    provider._http = http
    provider._account = browser
    provider._delivery = FakeDelivery()
    return provider, http, browser


def test_preview_bridges_http_version_to_the_browser_plan() -> None:
    provider, http, browser = _provider()

    plan = provider.preview_cart_update(
        [{"product_id": "new", "quantity": 1}],
        mode="merge",
        expected_version=http.cart.version,
        max_total=Decimal("5"),
    )

    assert plan["expected_http_cart_version"] == http.cart.version
    assert plan["plan_backend"] == "eroski_browser"
    assert browser.preview_expected_versions == [77]


def test_preview_rejects_a_stale_http_version_before_browser_work() -> None:
    provider, _, browser = _provider()

    with pytest.raises(ConcurrentCartChange):
        provider.preview_cart_update(
            [{"product_id": "new", "quantity": 1}],
            mode="merge",
            expected_version=123,
            max_total=Decimal("5"),
        )

    assert browser.preview_expected_versions == []


def test_commit_rechecks_http_version_before_browser_write() -> None:
    provider, http, browser = _provider()
    expected = http.cart.version
    plan = {
        "expected_http_cart_version": expected,
        "plan_backend": "eroski_browser",
        "desired_lines": [{"product_id": "old", "quantity": 1}],
        "estimated_total": 1.0,
    }

    result = provider.commit_cart_update(plan)

    assert result["committed"] is True
    assert result["http_post_write_verified"] is True
    assert browser.commits == 1


def test_commit_refuses_success_when_http_does_not_reflect_browser_write() -> None:
    provider, http, browser = _provider()
    plan = {
        "expected_http_cart_version": http.cart.version,
        "plan_backend": "eroski_browser",
        "desired_lines": [{"product_id": "new", "quantity": 1}],
        "estimated_total": 1.0,
    }

    with pytest.raises(ProviderError, match="does not match"):
        provider.commit_cart_update(plan)

    assert browser.commits == 1

    http.cart = EroskiCart(items=[EroskiCartItem("changed", 1)], total_text="2,00€")
    with pytest.raises(ConcurrentCartChange):
        provider.commit_cart_update(plan)
    assert browser.commits == 1


def test_http_failure_produces_one_coherent_browser_plan_and_commit() -> None:
    provider, http, browser = _provider()
    http.fail = True
    plan = provider.preview_cart_update(
        [{"product_id": "new", "quantity": 1}],
        mode="merge",
        expected_version=77,
        max_total=Decimal("5"),
    )
    assert plan["plan_backend"] == "eroski_browser_fallback"
    assert browser.preview_expected_versions == [77]
    result = provider.commit_cart_update(plan)
    assert result["committed"] is True
    assert browser.commits == 1


def test_http_browser_cart_mismatch_is_rejected_before_preview() -> None:
    provider, _, browser = _provider()
    browser.cart = lambda: {
        "version": 88,
        "total": 2.0,
        "lines": [{"product_id": "different", "quantity": 1}],
    }
    with pytest.raises(ConcurrentCartChange, match="do not match"):
        provider.preview_cart_update(
            [{"product_id": "new", "quantity": 1}],
            mode="merge",
            expected_version=provider._http.cart.version,
            max_total=Decimal("5"),
        )
    assert browser.preview_expected_versions == []


def test_delivery_delegates_to_the_get_only_selected_context_reader() -> None:
    provider, _, _ = _provider()

    assert provider.delivery_addresses() == [
        {"id": "address-1", "street_redacted": True}
    ]
    assert provider.delivery_slots("address-1") == [
        {"id": "slot-1", "available": True}
    ]


def test_account_status_marks_live_only_when_http_auth_succeeds() -> None:
    provider = object.__new__(EroskiFullProvider)
    provider._account = type(
        "Browser", (), {"status": lambda self: {"session_present": True}}
    )()
    provider._http = type(
        "HTTP",
        (),
        {
            "status": lambda self: {
                "authenticated": False,
                "http_session_checked": True,
            }
        },
    )()

    status = provider.account_status()

    assert status["authenticated_session"] is False
    assert status["validated_live"] is False
