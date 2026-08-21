from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any, Mapping, Sequence

from open_grocery_mcp.models import StoreInfo, as_decimal, money
from tools.gadis_mutation_verifier import MAX_ADDED_VALUE, verify


class FakeGadisProvider:
    info = StoreInfo(
        key="gadis",
        label="Gadis",
        country="ES",
        languages=("es",),
        capabilities=("account", "real_cart"),
    )

    def __init__(self, *, fail_after_apply_on: int | None = None) -> None:
        self.version = 10
        self.lines: dict[str, dict[str, Any]] = {
            "existing": {
                "product_id": "existing",
                "name": "Producto existente",
                "quantity": 1.0,
                "unit_price": 2.0,
            }
        }
        self.commit_calls = 0
        self.fail_after_apply_on = fail_after_apply_on

    def _cart(self) -> dict[str, Any]:
        lines = [deepcopy(line) for line in self.lines.values()]
        total = sum(
            as_decimal(line["quantity"]) * as_decimal(line["unit_price"])
            for line in lines
        )
        return {
            "store": "gadis",
            "cart_id": "cart-private",
            "store_id": "store-1",
            "version": self.version,
            "products_count": len(lines),
            "total": float(total),
            "total_text": money(total),
            "currency": "EUR",
            "lines": lines,
            "cart_backend": "gadis_http",
            "browser_driven": False,
        }

    def account_status(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "account_backend": "gadis_http",
            "http_session_checked": True,
        }

    def import_browser_session(self, storage_state_path: str) -> dict[str, Any]:
        return {"imported": bool(storage_state_path)}

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        return {"timeout": timeout_seconds}

    def real_cart(self) -> dict[str, Any]:
        return self._cart()

    def preview_cart_update(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        if expected_version is not None and expected_version != self.version:
            raise RuntimeError("version mismatch")
        desired = {} if mode == "replace" else deepcopy(self.lines)
        for change in changes:
            product_id = str(change["product_id"])
            quantity = as_decimal(change.get("quantity"))
            if quantity <= 0:
                desired.pop(product_id, None)
                continue
            desired[product_id] = {
                "product_id": product_id,
                "name": str(change.get("name", "")),
                "quantity": float(quantity),
                "unit_price": float(as_decimal(change.get("unit_price"))),
            }
        total = sum(
            as_decimal(line["quantity"]) * as_decimal(line["unit_price"])
            for line in desired.values()
        )
        if total > max_total:
            raise RuntimeError("cap exceeded")
        return {
            "store": "gadis",
            "expected_cart_version": self.version,
            "estimated_total": float(total),
            "estimated_total_text": money(total),
            "max_total": float(max_total),
            "desired_lines": list(desired.values()),
            "previous_lines": list(deepcopy(self.lines).values()),
            "plan_backend": "gadis_http",
        }

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        self.commit_calls += 1
        self.lines = {
            str(line["product_id"]): deepcopy(dict(line))
            for line in plan.get("desired_lines", [])
            if isinstance(line, Mapping)
        }
        self.version += 1
        if self.fail_after_apply_on == self.commit_calls:
            raise RuntimeError("simulated lost response")
        return self._cart()

    def close(self) -> None:
        pass


class FakeRegistry:
    def __init__(self, provider: FakeGadisProvider) -> None:
        self.provider = provider

    def get(self, key: str) -> FakeGadisProvider:
        assert key == "gadis"
        return self.provider

    def close(self) -> None:
        pass


def selected_product(*_: Any) -> dict[str, Any]:
    return {
        "product_id": "test-product",
        "name": "Producto de prueba",
        "price": Decimal("1.00"),
        "category": "Alimentación",
    }


def _safe_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False)


def test_reversible_verification_restores_the_original_cart(monkeypatch) -> None:
    _safe_environment(monkeypatch)
    provider = FakeGadisProvider()

    code, report = verify(
        allow_reversible_cart_write=True,
        registry=FakeRegistry(provider),
        product_selector=selected_product,
    )

    assert code == 0
    assert report["ok"] is True
    assert report["cart_restored"] is True
    assert report["confirmation_single_use"] is True
    assert all(report["steps"].values())
    assert report["write_attempts"] == 4
    assert set(provider.lines) == {"existing"}


def test_failure_after_an_ambiguous_write_still_removes_test_product(monkeypatch) -> None:
    _safe_environment(monkeypatch)
    provider = FakeGadisProvider(fail_after_apply_on=2)

    code, report = verify(
        allow_reversible_cart_write=True,
        registry=FakeRegistry(provider),
        product_selector=selected_product,
    )

    assert code == 1
    assert report["failure_stage"] == "quantity_2"
    assert report["failure_type"] == "RuntimeError"
    assert report["cart_restored"] is True
    assert set(provider.lines) == {"existing"}


def test_explicit_flag_and_write_environment_are_both_required(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    code, report = verify(allow_reversible_cart_write=False)
    assert code == 2
    assert report["retailer_write_performed"] is False

    monkeypatch.delenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", raising=False)
    code, report = verify(allow_reversible_cart_write=True)
    assert code == 2
    assert report["retailer_write_performed"] is False


def test_order_submission_opt_ins_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")

    code, report = verify(allow_reversible_cart_write=True)

    assert code == 2
    assert "order-submission" in report["reason"]
    assert report["order_or_payment_attempted"] is False


def test_temporary_added_value_has_a_hard_five_euro_limit(monkeypatch) -> None:
    _safe_environment(monkeypatch)

    code, _ = verify(
        allow_reversible_cart_write=True,
        max_added_value=MAX_ADDED_VALUE + Decimal("0.01"),
    )

    assert code == 2


def test_existing_product_candidate_is_rejected_without_touching_it(monkeypatch) -> None:
    _safe_environment(monkeypatch)
    provider = FakeGadisProvider()

    def existing(*_: Any) -> dict[str, Any]:
        return {
            "product_id": "existing",
            "name": "Producto existente",
            "price": Decimal("1.00"),
            "category": "Alimentación",
        }

    code, report = verify(
        allow_reversible_cart_write=True,
        registry=FakeRegistry(provider),
        product_selector=existing,
    )

    assert code == 1
    assert report["failure_stage"] == "preflight"
    assert report["retailer_write_performed"] is False
    assert set(provider.lines) == {"existing"}
