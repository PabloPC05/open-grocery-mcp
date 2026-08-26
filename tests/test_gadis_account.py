from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import pytest

from open_grocery_mcp.errors import (
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    ProviderError,
    UnsupportedOperation,
)
from open_grocery_mcp.providers.gadis_account import GadisAccountClient
from open_grocery_mcp.providers.gadis_http import GadisHTTPClient


class FakeBrowser:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.preview_calls = 0
        self.commit_calls = 0
        self.address_calls = 0
        self.external_checkouts: dict[str, dict[str, Any]] = {}
        self.fail_remember_checkout = False

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

    def addresses(self):
        self.address_calls += 1
        return [{"id": "browser-address"}]

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

    def remember_external_checkout(self, checkout_id, snapshot, *, backend):
        if self.fail_remember_checkout:
            raise OSError("simulated private store failure")
        self.external_checkouts[str(checkout_id)] = {
            "backend": backend,
            "snapshot": deepcopy(snapshot),
        }

    def external_checkout_snapshot(self, checkout_id, *, backend):
        record = self.external_checkouts.get(str(checkout_id))
        if record is None or record["backend"] != backend:
            return None
        return deepcopy(record["snapshot"])

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
            "total_product_price": 2.0,
            "costs": 0.0,
            "total_cart_price": 2.0,
            "total_products": 1,
            "last_modified_date": 10,
        }
        self.prices = {"p-old": 2.0, "p-new": 1.5}
        self.actual_prices: dict[str, float] = {}
        self.update_calls: list[tuple[str, int]] = []
        self.address_calls = 0
        self.client_address_calls = 0
        self.address_rows: list[dict[str, Any]] = []
        self.schedule_calls: list[tuple[str, str]] = []
        self.delete_schedule_calls = 0
        self.checkout_calls = 0
        self.summary_calls = 0
        self.fail_summary = False
        self.fail_delete_schedule = False
        self.calendar = [
            {
                "id": "slot-9",
                "date": "2026-08-25",
                "start": "10:00",
                "end": "11:00",
                "available": True,
                "active": True,
                "max_lines": 8,
            }
        ]
        self.raise_after_apply = False
        self.invalidated = 0

    def _recalculate(self) -> None:
        product_total = 0.0
        for line in self.raw["products"]:
            product_total += float(line["line_price"])
        self.raw["total_product_price"] = round(product_total, 2)
        self.raw["total_cart_price"] = round(
            product_total + float(self.raw.get("costs", 0.0)), 2
        )
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

    def addresses(self, cart_id: str) -> list[dict[str, Any]]:
        assert cart_id == "cart-1"
        self.address_calls += 1
        return deepcopy(self.address_rows)

    def address_contexts(self, cart_id: str) -> list[dict[str, Any]]:
        return self.addresses(cart_id)

    def client_addresses(self) -> list[dict[str, Any]]:
        self.client_address_calls += 1
        return deepcopy(self.address_rows)

    def client_address_contexts(self) -> list[dict[str, Any]]:
        return self.client_addresses()

    def delivery_slots(self, postal_code=None, *, store_id=None, **_: Any) -> list[dict[str, Any]]:
        return deepcopy(self.calendar)

    def update_schedule(
        self,
        cart_id: str,
        store_id: str,
        *,
        delivery_date: str,
        schedule_range_id,
        postal_code=None,
        shipping_address_id=None,
        shipping_address_owner=None,
    ) -> dict[str, Any]:
        self.schedule_calls.append(("put", cart_id, str(schedule_range_id)))
        if postal_code:
            self.raw["postal_code"] = postal_code
        if shipping_address_id is not None:
            self.raw["shipping_address_id"] = shipping_address_id
            self.raw["shipping_address_owner"] = shipping_address_owner
        self.raw["delivery_date"] = delivery_date
        self.raw["schedule_range_id"] = schedule_range_id
        return self.normalize_cart(self.raw)

    def delete_schedule(self, cart_id: str) -> None:
        self.delete_schedule_calls += 1
        if self.fail_delete_schedule:
            raise ProviderError("simulated schedule deletion failure")
        self.raw["delivery_date"] = None
        self.raw["schedule_range_id"] = None
        self.raw["shipping_address_id"] = None
        self.raw["shipping_address_owner"] = None
        return None

    def restore_cart_context(self, baseline: Mapping[str, Any]) -> dict[str, Any]:
        for key in (
            "postal_code",
            "delivery_type",
            "comments",
            "shipping_address_id",
            "shipping_address_owner",
            "delivery_date",
            "schedule_range_id",
        ):
            self.raw[key] = baseline.get(key)
        return self.normalize_cart(self.raw)

    def prepare_checkout_summary(
        self,
        cart_id: str,
        store_id: str,
        *,
        shipping_address_id,
        shipping_address_owner=None,
        delivery_date: str,
        schedule_range_id,
        **_: Any,
    ) -> dict[str, Any]:
        self.summary_calls += 1
        if self.fail_summary:
            raise ProviderError("simulated summary failure")
        self.raw["shipping_address_id"] = shipping_address_id
        self.raw["shipping_address_owner"] = shipping_address_owner
        self.raw["delivery_date"] = delivery_date
        self.raw["schedule_range_id"] = schedule_range_id
        return {**self.normalize_cart(self.raw), "summary_prepared": True}

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
                    )
                    * amount,
                }
            )
        else:
            existing["amount"] = amount
            existing["line_price"] = (
                self.actual_prices.get(product_id, self.prices[product_id]) * amount
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


def test_gadis_plan_includes_current_non_product_costs(tmp_path: Path) -> None:
    account, http, _ = _account(tmp_path)
    http.raw["costs"] = 4.5
    http._recalculate()

    with pytest.raises(BudgetExceeded, match="9.00 EUR"):
        account.preview_cart_update(
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
            max_total=Decimal("9"),
        )

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
        max_total=Decimal("10"),
    )

    assert plan["non_product_costs"] == 4.5
    assert plan["estimated_total_text"] == "9.50"
    result = account.commit_cart_update(plan)
    assert result["total"] == 9.5
    new_line = next(line for line in result["lines"] if line["product_id"] == "p-new")
    assert new_line["quantity"] == 2.0
    assert new_line["unit_price"] == 1.5
    assert new_line["line_total"] == 3.0
    assert http.update_calls == [("p-new", 2)]


def test_gadis_commit_rejects_changed_non_product_costs_before_writing(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    http.raw["costs"] = 4.5
    http._recalculate()
    plan = account.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="merge",
        expected_version=account.cart()["version"],
        max_total=Decimal("10"),
    )
    http.raw["costs"] = 5.5
    http._recalculate()

    with pytest.raises(ConcurrentCartChange, match="changed from version"):
        account.commit_cart_update(plan)

    assert http.update_calls == []


def test_gadis_changed_non_product_costs_are_rolled_back_after_write(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    http.raw["costs"] = 4.5
    http._recalculate()
    plan = account.preview_cart_update(
        [{"product_id": "p-new", "quantity": 1, "unit_price": 1.5}],
        mode="merge",
        expected_version=account.cart()["version"],
        max_total=Decimal("12"),
    )
    original_update = http.update_product

    def update_with_cost_change(
        cart_id: str, store_id: str, product_id: str, amount: int, **kwargs: Any
    ) -> dict[str, Any]:
        result = original_update(cart_id, store_id, product_id, amount, **kwargs)
        if product_id == "p-new":
            http.raw["costs"] = 5.5 if amount else 4.5
            http._recalculate()
        return result

    http.update_product = update_with_cost_change  # type: ignore[method-assign]

    with pytest.raises(ProviderError, match="previous cart restored"):
        account.commit_cart_update(plan)

    assert http.update_calls == [("p-new", 1), ("p-new", 0)]
    assert http.raw["costs"] == 4.5
    assert http.raw["total_cart_price"] == 6.5


def test_commit_rejects_a_tampered_gadis_plan_before_writing(tmp_path: Path) -> None:
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
    plan["desired_lines"][0]["quantity"] = -1

    with pytest.raises(InvalidRequest, match="invalid desired line"):
        account.commit_cart_update(plan)

    assert http.update_calls == []


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


@pytest.mark.parametrize("quantity", [-1, "bad", True, 1001])
def test_invalid_gadis_quantity_is_rejected_before_any_fallback(
    tmp_path: Path, quantity: object
) -> None:
    account, http, browser = _account(tmp_path)
    with pytest.raises(InvalidRequest, match="quantity"):
        account.preview_cart_update(
            [{"product_id": "p-new", "name": "Leche", "quantity": quantity}],
            mode="merge",
            expected_version=None,
            max_total=Decimal("10"),
        )
    assert http.update_calls == []
    assert browser.preview_calls == 0


def test_duplicate_gadis_changes_are_rejected(tmp_path: Path) -> None:
    account, http, browser = _account(tmp_path)
    with pytest.raises(InvalidRequest, match="duplicate"):
        account.preview_cart_update(
            [
                {"product_id": "p-new", "quantity": 1, "unit_price": 1.5},
                {"product_id": "p-new", "quantity": 2, "unit_price": 1.5},
            ],
            mode="merge",
            expected_version=account.cart()["version"],
            max_total=Decimal("10"),
        )
    assert http.update_calls == []
    assert browser.preview_calls == 0


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


def test_gadis_rolls_back_price_change_even_when_still_below_cap(
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
    http.actual_prices["p-new"] = 2.0
    with pytest.raises(ProviderError, match="previous cart restored"):
        account.commit_cart_update(plan)
    assert [(line["product_id"], line["amount"]) for line in http.raw["products"]] == [
        ("p-old", 1)
    ]


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


def test_ambiguous_write_does_not_restore_an_unreviewed_concurrent_cart(
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
    original_update = http.update_product
    concurrent_injected = False

    def update_then_concurrent(*args: Any, **kwargs: Any):
        nonlocal concurrent_injected
        try:
            return original_update(*args, **kwargs)
        finally:
            if not concurrent_injected:
                concurrent_injected = True
                http.raw["products"] = [
                    {
                        "product_id": "p-concurrent",
                        "product_name": "Cambio concurrente",
                        "amount": 1,
                        "line_price": 3.0,
                    }
                ]
                http._recalculate()

    http.update_product = update_then_concurrent  # type: ignore[method-assign]

    with pytest.raises(ConcurrentCartChange, match="unreviewed state"):
        account.commit_cart_update(plan)

    assert http.update_calls == [("p-new", 1)]
    assert [line["product_id"] for line in http.raw["products"]] == [
        "p-concurrent"
    ]


def test_gadis_rereads_after_each_http_mutation_and_stops_on_concurrency(
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
        mode="replace",
        expected_version=account.cart()["version"],
        max_total=Decimal("5"),
    )
    original_update = http.update_product

    def remove_then_concurrent(*args: Any, **kwargs: Any):
        result = original_update(*args, **kwargs)
        if args[2] == "p-old" and args[3] == 0:
            http.raw["products"].append(
                {
                    "product_id": "p-concurrent",
                    "product_name": "Cambio concurrente",
                    "amount": 1,
                    "line_price": 3.0,
                }
            )
            http._recalculate()
        return result

    http.update_product = remove_then_concurrent  # type: ignore[method-assign]

    with pytest.raises(ConcurrentCartChange, match="unreviewed state"):
        account.commit_cart_update(plan)

    assert http.update_calls == [("p-old", 0)]
    assert [line["product_id"] for line in http.raw["products"]] == [
        "p-concurrent"
    ]


def test_gadis_rollback_rechecks_version_before_its_first_write(
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
    http.actual_prices["p-new"] = 2.0
    original_restore = account._restore_http_cart

    def concurrent_before_restore(*args: Any, **kwargs: Any) -> None:
        http.raw["products"] = [
            {
                "product_id": "p-concurrent",
                "product_name": "Cambio concurrente",
                "amount": 1,
                "line_price": 3.0,
            }
        ]
        http._recalculate()
        original_restore(*args, **kwargs)

    account._restore_http_cart = concurrent_before_restore  # type: ignore[method-assign]

    with pytest.raises(ProviderError, match="rollback could not be verified"):
        account.commit_cart_update(plan)

    assert http.update_calls == [("p-new", 1)]
    assert [line["product_id"] for line in http.raw["products"]] == [
        "p-concurrent"
    ]


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


def _http_checkout_plan(account: GadisAccountClient) -> dict[str, Any]:
    account._http.address_rows = [
        {"id": "addr-1", "owner": "CLIENT", "postal_code": "28050"}
    ]
    version = account.cart()["version"]
    return {
        "reviewed_cart_backend": "gadis_http",
        "expected_cart_version": version,
        "max_total": 50.0,
        "delivery": {
            "shipping_address_id": "addr-1",
            "shipping_address_owner": "CLIENT",
            "delivery_date": "2026-08-25",
            "schedule_range_id": "slot-9",
        },
    }


def test_http_checkout_sets_schedule_then_prepares_summary(tmp_path: Path) -> None:
    account, http, browser = _account(tmp_path)
    result = account.create_checkout(_http_checkout_plan(account))
    assert http.schedule_calls == [("put", "cart-1", "slot-9")]
    assert http.summary_calls == 1
    assert http.checkout_calls == 0
    assert browser.commit_calls == 0
    assert result["checkout_backend"] == "gadis_http"
    assert result["order_placed"] is False
    assert result["checkout_id"].startswith("gadis-summary-")
    assert result["summary_prepared"] is True

    reread = account.get_checkout(result["checkout_id"])
    assert reread["authoritative_cart_reread"] is True
    assert reread["address_id"] == "addr-1"
    assert reread["slot_id"] == "slot-9"
    assert "_reviewed_lines" not in reread


def test_http_checkout_reread_rejects_same_total_with_changed_lines(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    checkout = account.create_checkout(_http_checkout_plan(account))
    http.raw["products"][0]["product_id"] = "different-product"

    with pytest.raises(ConcurrentCartChange, match="changed after HTTP checkout"):
        account.get_checkout(checkout["checkout_id"])


def test_http_checkout_never_falls_through_to_unverified_submission(
    tmp_path: Path,
) -> None:
    account, _, browser = _account(tmp_path)
    checkout = account.create_checkout(_http_checkout_plan(account))

    with pytest.raises(UnsupportedOperation, match="finish it manually"):
        account.submit_order(checkout["checkout_id"], max_total=Decimal("5"))
    with pytest.raises(UnsupportedOperation, match="fixed at creation"):
        account.set_checkout_delivery(
            checkout["checkout_id"],
            address_id="addr-1",
            slot_id="slot-9",
            max_total=Decimal("5"),
        )
    assert browser.commit_calls == 0


def test_http_checkout_reread_survives_account_recreation(tmp_path: Path) -> None:
    account, http, browser = _account(tmp_path)
    checkout = account.create_checkout(_http_checkout_plan(account))

    recreated = GadisAccountClient(browser=browser, http=http)
    reread = recreated.get_checkout(checkout["checkout_id"])

    assert reread["authoritative_cart_reread"] is True
    assert reread["checkout_backend"] == "gadis_http"


def test_http_checkout_persistence_failure_is_never_retryable_as_creation(
    tmp_path: Path,
) -> None:
    account, http, browser = _account(tmp_path)
    browser.fail_remember_checkout = True

    with pytest.raises(ProviderError, match="inspect the cart"):
        account.create_checkout(_http_checkout_plan(account))

    assert http.summary_calls == 1
    assert http.checkout_calls == 0


def test_http_checkout_rolls_schedule_back_when_creation_fails(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    http.fail_summary = True
    with pytest.raises(ProviderError):
        account.create_checkout(_http_checkout_plan(account))
    assert http.summary_calls == 1
    assert http.checkout_calls == 0
    assert http.delete_schedule_calls == 1


def test_http_checkout_reports_unverified_schedule_rollback(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    http.fail_summary = True
    http.fail_delete_schedule = True
    with pytest.raises(ProviderError, match="rollback could not be verified"):
        account.create_checkout(_http_checkout_plan(account))
    assert http.delete_schedule_calls == 1


def test_browser_checkout_requires_line_prices_to_match_http(tmp_path: Path) -> None:
    account, _, browser = _account(tmp_path)
    plan = account.preview_checkout(
        expected_version=account.cart()["version"], max_total=Decimal("5")
    )
    def changed_cart():
        return {
            "version": 99,
            "total": 2.0,
            "lines": [
                {
                    "product_id": "p-old",
                    "name": "Arroz",
                    "quantity": 1,
                    "unit_price": 1.5,
                }
            ],
        }

    browser.cart = changed_cart
    with pytest.raises(ConcurrentCartChange, match="does not match"):
        account.create_checkout(plan)
    assert browser.commit_calls == 0


def test_http_checkout_rejects_unavailable_slot_before_writes(
    tmp_path: Path,
) -> None:
    account, http, _ = _account(tmp_path)
    http.calendar[0]["available"] = False
    with pytest.raises(InvalidRequest, match="not currently available"):
        account.create_checkout(_http_checkout_plan(account))
    assert http.schedule_calls == []
    assert http.checkout_calls == 0


def test_http_checkout_refuses_changed_cart_version(tmp_path: Path) -> None:
    account, http, _ = _account(tmp_path)
    plan = _http_checkout_plan(account)
    plan["expected_cart_version"] = plan["expected_cart_version"] + 999
    with pytest.raises(ConcurrentCartChange):
        account.create_checkout(plan)
    assert http.schedule_calls == []
    assert http.checkout_calls == 0


def test_gadis_delivery_prefers_http_and_checkout_stays_gated(tmp_path: Path) -> None:
    account, http, browser = _account(tmp_path)
    # Empty HTTP results stay empty: no silent browser fallback without error.
    assert account.addresses() == []
    assert browser.address_calls == 0
    assert http.address_calls == 1
    assert http.client_address_calls == 1
    status = account.status()
    assert status["delivery_backend"] == "gadis_http_with_browser_fallback"
    assert status["checkout_backend"] == "gadis_http_with_browser_fallback"

    http.status = lambda: {
        "authenticated": False,
        "http_session_checked": True,
    }
    assert account.status()["validated_live"] is False


def test_session_replacement_invalidates_cached_http_state(tmp_path: Path) -> None:
    account, http, _ = _account(tmp_path)
    account.import_storage_state("new-state.json")
    account.login_with_browser(timeout_seconds=12)
    assert http.invalidated == 2
    status = account.status()
    assert status["validated_live"] is True
    assert status["cart_backend"] == "gadis_http_with_browser_fallback"
