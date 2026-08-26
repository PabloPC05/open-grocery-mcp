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
    InvalidRequest,
    ProviderError,
    UnsupportedOperation,
)
from open_grocery_mcp.providers.froiz_account import FroizAccountClient
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient

PRICES = {"p-old": 2.0, "p-new": 1.5}


def _processed(
    cart_id: str | None,
    items: list[dict[str, Any]],
    prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    price_map = prices or PRICES
    return {
        "id": cart_id or "",
        "items": [
            {
                "comment": item["comment"],
                "enabled": True,
                "product": {
                    "id": item["product_id"],
                    "name": f"N-{item['product_id']}",
                    "price": price_map.get(item["product_id"], 1.0),
                },
                "qty": item["qty"],
                "unit": item["unit"],
                **({"units": item["units"]} if "units" in item else {}),
            }
            for item in items
        ],
        "total": round(
            sum(price_map.get(i["product_id"], 1.0) * i["qty"] for i in items), 2
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
        self.actual_prices: dict[str, float] | None = None

    def channel_cart_id(self) -> str | None:
        if self.fail_channel:
            raise AuthenticationRequired("expired")
        return self.cart_id

    def raw_cart(self, cart_id: str) -> dict[str, Any]:
        if self.cart_id is None:
            raise ProviderError("Froiz GET cart returned HTTP 404")
        assert cart_id == self.cart_id
        return _processed(cart_id, self.items, self.actual_prices)

    def processed_cart(self, cart_id: str) -> dict[str, Any]:
        return self.raw_cart(cart_id)

    def create_cart(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(("create", deepcopy(items)))
        if self.fail_update:
            raise ProviderError("simulated create failure")
        self.cart_id = "cart-new"
        self.items = deepcopy(items)
        if self.mutate_response:
            self.items.append(
                {"product_id": "p-rogue", "qty": 1.0, "unit": "ud", "comment": ""}
            )
        return _processed(self.cart_id, self.items, self.actual_prices)

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
        self.items = deepcopy(stored)
        return _processed(cart_id, stored, self.actual_prices)

    def delete_cart(self, cart_id: str) -> None:
        self.calls.append(("delete", []))
        if cart_id == self.cart_id:
            self.cart_id = None
            self.items = []

    def status(self) -> dict[str, Any]:
        return {
            "store": "froiz",
            "session_present": True,
            "authenticated": True,
        }

    def invalidate_session(self) -> None:
        pass

    def addresses(self) -> list[dict[str, Any]]:
        self.address_calls = getattr(self, "address_calls", 0) + 1
        return [{"id": "addr-http", "is_default": True}]

    def delivery_calendar(self, postal_code=None):
        self.calendar_calls = getattr(self, "calendar_calls", 0) + 1
        self.last_postal_code = postal_code
        return [{"date": "2026-08-22", "available": True}]

    def postal_code_for_address(self, address_id: str | int) -> str:
        assert str(address_id) == "addr-http"
        return "28050"

    def default_postal_code(self, **kwargs: object) -> str:
        assert kwargs == {"allow_browser_refresh": False}
        return "28050"

    def store_by_postal_code(
        self,
        postal_code: str,
        **kwargs: object,
    ) -> dict[str, Any]:
        assert postal_code == "28050"
        assert kwargs == {"allow_browser_refresh": False}
        self.store_lookup_calls = getattr(self, "store_lookup_calls", 0) + 1
        return {"codEnt": "E1", "codSubent": "S2"}

    def search_products(
        self,
        query: str,
        *,
        store: str,
        size: int,
        **kwargs: object,
    ) -> list[dict[str, Any]]:
        assert query == "leche"
        assert store == "E1_S2"
        assert size == 3
        assert kwargs == {"allow_browser_refresh": False}
        return [{"id": "p-milk", "name": "Leche", "order_price": 1.65}]

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

    def preview_cart_update(self, changes, **kwargs):
        return {"browser_preview": True, "changes": list(changes), **kwargs}

    def addresses(self):
        return []

    def slots(self, address_id):
        return []

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


def test_authenticated_search_resolves_default_store(account) -> None:
    client, fake, _ = account

    products = client.search_products("leche", limit=3)
    repeated = client.search_products("leche", limit=3)

    assert products == [{"id": "p-milk", "name": "Leche", "order_price": 1.65}]
    assert repeated == products
    assert fake.store_lookup_calls == 1


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
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="replace",
        expected_version=version,
        max_total=Decimal("10"),
    )
    assert [i["product_id"] for i in plan["desired_items"]] == ["p-new"]


def test_commit_rejects_a_tampered_froiz_plan_before_writing(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="replace",
        expected_version=version,
        max_total=Decimal("10"),
    )
    plan["desired_items"][0]["qty"] = -1

    with pytest.raises(InvalidRequest, match="invalid quantity"):
        client.commit_cart_update(plan)

    assert fake.calls == []


def test_preview_rejects_stale_version(account) -> None:
    client, fake, _ = account
    with pytest.raises(ConcurrentCartChange):
        client.preview_cart_update(
            [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
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


@pytest.mark.parametrize("quantity", [-1, "bad", True, 1001])
def test_preview_rejects_unsafe_quantities(account, quantity: object) -> None:
    client, fake, browser = account
    with pytest.raises(InvalidRequest, match="quantity"):
        client.preview_cart_update(
            [
                {
                    "product_id": "p-new",
                    "quantity": quantity,
                    "unit_price": 1.5,
                }
            ],
            mode="merge",
            expected_version=client.real_cart()["version"],
            max_total=Decimal("10"),
        )
    assert fake.calls == []
    assert browser.commit_calls == 0


def test_preview_rejects_duplicate_changes_and_missing_prices(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    with pytest.raises(InvalidRequest, match="duplicate"):
        client.preview_cart_update(
            [
                {"product_id": "p-new", "quantity": 1, "unit_price": 1.5},
                {"product_id": "p-new", "quantity": 2, "unit_price": 1.5},
            ],
            mode="merge",
            expected_version=version,
            max_total=Decimal("10"),
        )
    with pytest.raises(InvalidRequest, match="without positive prices"):
        client.preview_cart_update(
            [{"product_id": "p-new", "quantity": 1}],
            mode="merge",
            expected_version=version,
            max_total=Decimal("10"),
        )
    assert fake.calls == []


def test_commit_updates_existing_cart_over_http(account) -> None:
    client, fake, browser = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2, "unit_price": 1.5}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    result = client.commit_cart_update(plan)
    assert ("update", plan["desired_items"]) in fake.calls
    assert browser.commit_calls == 0
    assert result["cart_backend"] == "froiz_http"
    assert result["order_placed"] is False


def test_whole_object_update_preserves_optional_units_field(account) -> None:
    client, fake, _ = account
    fake.items[0]["units"] = 1
    version = client.real_cart()["version"]

    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    client.commit_cart_update(plan)

    update = next(items for operation, items in fake.calls if operation == "update")
    retained = next(item for item in update if item["product_id"] == "p-old")
    assert retained["units"] == 1


def test_replace_preserves_units_for_a_retained_product(account) -> None:
    client, fake, _ = account
    fake.items[0]["units"] = 1
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-old", "quantity": 2, "unit_price": 2.0}],
        mode="replace",
        expected_version=version,
        max_total=Decimal("10"),
    )

    client.commit_cart_update(plan)

    update = next(items for operation, items in fake.calls if operation == "update")
    assert update == [
        {
            "product_id": "p-old",
            "qty": 2.0,
            "unit": "ud",
            "comment": "",
            "units": 1,
        }
    ]


def test_units_are_part_of_cart_concurrency_signature(account) -> None:
    client, fake, _ = account
    fake.items[0]["units"] = 1
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    fake.items[0]["units"] = 2

    with pytest.raises(ConcurrentCartChange, match="changed from version"):
        client.commit_cart_update(plan)

    assert fake.calls == []


def test_tampered_optional_units_are_not_accepted(account) -> None:
    client, fake, _ = account
    fake.items[0]["units"] = 1
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    plan["previous_items"][0]["units"] = 99

    with pytest.raises(ConcurrentCartChange, match="contents changed"):
        client.commit_cart_update(plan)

    assert fake.calls == []


def test_commit_creates_when_no_cart_is_bound(account) -> None:
    client, fake, _ = account
    fake.cart_id = None
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="merge",
        expected_version=None,
        max_total=Decimal("10"),
    )
    result = client.commit_cart_update(plan)
    assert [op for op, _ in fake.calls] == ["create"]
    assert result["cart_id"] == "cart-new"


def test_commit_rejects_swapped_line_prices_even_when_total_is_unchanged(
    account,
) -> None:
    client, fake, _ = account
    fake.cart_id = None
    fake.items = []
    plan = client.preview_cart_update(
        [
            {"product_id": "p-old", "quantity": 1, "unit_price": 2.0},
            {"product_id": "p-new", "quantity": 1, "unit_price": 1.5},
        ],
        mode="replace",
        expected_version=None,
        max_total=Decimal("10"),
    )
    fake.actual_prices = {"p-old": 1.5, "p-new": 2.0}

    with pytest.raises(ProviderError):
        client.commit_cart_update(plan)

    assert [operation for operation, _ in fake.calls] == ["create", "delete"]


def test_failed_new_cart_verification_deletes_the_known_disposable_cart(account) -> None:
    client, fake, _ = account
    fake.cart_id = None
    fake.items = []
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="merge",
        expected_version=None,
        max_total=Decimal("10"),
    )
    fake.mutate_response = True
    with pytest.raises(ProviderError, match="disposable cart removed"):
        client.commit_cart_update(plan)
    assert [operation for operation, _ in fake.calls] == ["create", "delete"]
    assert fake.cart_id is None


def test_commit_detects_concurrent_change_before_writing(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
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


def test_failed_write_does_not_rewrite_an_unchanged_cart(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2, "unit_price": 1.5}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    fake.fail_update = True
    with pytest.raises(ProviderError, match="previous cart remained unchanged"):
        client.commit_cart_update(plan)
    updates = [items for op, items in fake.calls if op == "update"]
    assert len(updates) == 1


def test_commit_never_overwrites_an_unknown_existing_cart_state(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2, "unit_price": 1.5}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    fake.mutate_response = True
    with pytest.raises(ProviderError, match="inspect the retailer cart"):
        client.commit_cart_update(plan)
    updates = [items for op, items in fake.calls if op == "update"]
    assert updates == [plan["desired_items"]]
    assert fake.items != plan["previous_items"]


def test_delivery_reads_use_http_first(account) -> None:
    client, fake, browser = account
    addresses = client.addresses()
    assert addresses == [{"id": "addr-http", "is_default": True}]
    assert fake.address_calls == 1
    slots = client.slots("addr-http")
    assert len(slots) == 1 and slots[0]["available"] is True
    assert fake.calendar_calls == 1
    assert fake.last_postal_code == "28050"
    status = client.status()
    assert status["delivery_backend"] == "froiz_http_with_browser_fallback"
    assert status["checkout_backend"] == "browser_blocked_by_design"
    assert status["validated_live"] is True

    fake.status = lambda: {
        "store": "froiz",
        "session_present": True,
        "authenticated": False,
        "http_session_checked": True,
    }
    assert client.status()["validated_live"] is False


def test_account_never_touches_order_or_payment_endpoints(account) -> None:
    client, fake, _ = account
    version = client.real_cart()["version"]
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 2, "unit_price": 1.5}],
        mode="merge",
        expected_version=version,
        max_total=Decimal("10"),
    )
    client.commit_cart_update(plan)
    recorded = json.dumps(fake.calls, default=str)
    assert "/orders" not in recorded
    assert "/api/payment" not in recorded


def test_preview_falls_back_as_one_coherent_browser_plan(account) -> None:
    client, fake, browser = account
    fake.fail_channel = True
    plan = client.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="merge",
        expected_version=None,
        max_total=Decimal("10"),
    )
    assert plan["plan_backend"] == "browser"
    result = client.commit_cart_update(plan)
    assert result["browser_commit"] is True
    assert browser.commit_calls == 1


def test_froiz_checkout_and_order_boundary_is_explicitly_blocked(account) -> None:
    client, _, _ = account
    with pytest.raises(UnsupportedOperation, match="blocked"):
        client.preview_checkout(expected_version=None, max_total=Decimal("10"))
    with pytest.raises(UnsupportedOperation, match="places the real order"):
        client.create_checkout({})
    with pytest.raises(UnsupportedOperation, match="blocked by design"):
        client.submit_order("checkout", max_total=Decimal("10"))


