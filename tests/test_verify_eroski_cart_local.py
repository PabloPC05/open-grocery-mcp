from __future__ import annotations

from open_grocery_mcp.errors import AuthenticationRequired
from open_grocery_mcp.providers.eroski_http import (
    EroskiCart,
    EroskiCartItem,
    TileConfig,
)
from tools.verify_eroski_cart_local import verify


def _tile(product_ref: str) -> TileConfig:
    return TileConfig(
        item_id=f"item-{product_ref}",
        product_ref=product_ref,
        shop_ref="shop",
        previous_address_ref=None,
        quantity_in_cart=0,
        maximum_quantity=10,
        product_units_per_pack=1,
        is_weight_options_available=False,
        on_add_to_cart_event="/safe:addtocart",
    )


class FakeHTTP:
    def __init__(
        self,
        *,
        fail_after_add_read: bool = False,
        concurrent_after_add_read: bool = False,
    ) -> None:
        self.items: dict[str, int] = {"existing": 1}
        self.fail_after_add_read = fail_after_add_read
        self.concurrent_after_add_read = concurrent_after_add_read
        self.read_calls = 0

    def read_cart(self) -> EroskiCart:
        self.read_calls += 1
        if self.fail_after_add_read and self.read_calls == 2:
            raise AuthenticationRequired("simulated expired session")
        if self.concurrent_after_add_read and self.read_calls == 2:
            self.items["concurrent"] = 1
        total = "3,00€" if "probe" in self.items else "1,50€"
        return EroskiCart(
            items=[EroskiCartItem(pid, qty) for pid, qty in self.items.items()],
            total_text=total,
        )

    def search_tiles(self, _: str) -> list[TileConfig]:
        return [_tile("existing"), _tile("probe")]


class FakeProvider:
    def __init__(
        self,
        *,
        fail_after_add_read: bool = False,
        concurrent_after_add_read: bool = False,
        ambiguous_remove: bool = False,
        pre_click_rejection: bool = False,
        ambiguous_add: bool = False,
    ) -> None:
        self._http = FakeHTTP(
            fail_after_add_read=fail_after_add_read,
            concurrent_after_add_read=concurrent_after_add_read,
        )
        self.ambiguous_remove = ambiguous_remove
        self.pre_click_rejection = pre_click_rejection
        self.ambiguous_add = ambiguous_add
        self.add_indexes: list[int] = []
        self.add_refs: list[str | None] = []
        self.removed: list[str] = []

    def add_item_via_browser(
        self,
        _: str,
        *,
        tile_index: int = 0,
        max_price=None,
        expected_product_ref: str | None = None,
    ):
        self.add_indexes.append(tile_index)
        self.add_refs.append(expected_product_ref)
        if self.pre_click_rejection:
            return {
                "added": False,
                "write_attempted": False,
                "reason": "expected product reference not found in rendered tiles",
            }
        self._http.items["probe"] = 1
        if self.ambiguous_add:
            return {
                "added": False,
                "write_attempted": True,
                "product_price": "1.50",
                "reason": "cart total did not change after the click",
            }
        return {
            "added": True,
            "write_attempted": True,
            "product_price": "1.50",
        }

    def remove_item_via_browser(self, product_id: str, *, max_clicks: int = 6):
        self.removed.append(product_id)
        self._http.items.pop(product_id, None)
        if self.ambiguous_remove:
            raise RuntimeError("simulated ambiguous browser removal")
        return {"removed_clicks": 1, "rows_left": 0}


class FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider
        self.closed = False

    def get(self, key: str) -> FakeProvider:
        assert key == "eroski"
        return self.provider

    def close(self) -> None:
        self.closed = True


def _enable_safe_writes(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv(
        "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION",
        raising=False,
    )


def test_verifier_uses_browser_writes_and_restores_the_cart(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    provider = FakeProvider()
    registry = FakeRegistry(provider)

    code, report = verify(
        allow_reversible_cart_write=True,
        registry=registry,
    )

    assert code == 0
    assert report["ok"] is True
    assert report["steps"]["state_restored"] is True
    assert provider.add_indexes == [1]
    assert provider.add_refs == ["probe"]
    assert report["write_attempted"] is True
    assert report["added_observed"] is True
    assert provider.removed == ["probe"]
    assert provider._http.items == {"existing": 1}
    assert registry.closed is True


def test_verifier_cleans_up_when_the_post_add_read_loses_auth(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    provider = FakeProvider(fail_after_add_read=True)
    registry = FakeRegistry(provider)

    code, report = verify(
        allow_reversible_cart_write=True,
        registry=registry,
    )

    assert code == 1
    assert report["failure_stage"] == "add"
    assert report["emergency_cleanup_attempted"] is True
    assert report["emergency_cleanup_restored"] is True
    assert provider.removed == ["probe"]
    assert provider._http.items == {"existing": 1}


def test_verifier_does_not_arm_cleanup_when_add_is_rejected_before_click(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    provider = FakeProvider(pre_click_rejection=True)
    registry = FakeRegistry(provider)

    code, report = verify(allow_reversible_cart_write=True, registry=registry)

    assert code == 1
    assert report["failure_stage"] == "add"
    assert report["write_attempted"] is False
    assert report["added_observed"] is False
    assert report["retailer_write_performed"] is False
    assert "emergency_cleanup_attempted" not in report
    assert provider.removed == []


def test_verifier_reports_and_cleans_up_a_click_with_ambiguous_add_result(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    provider = FakeProvider(ambiguous_add=True)
    registry = FakeRegistry(provider)

    code, report = verify(allow_reversible_cart_write=True, registry=registry)

    assert code == 1
    assert report["failure_stage"] == "add"
    assert report["write_attempted"] is True
    assert report["added_observed"] is False
    assert report["retailer_write_performed"] is True
    assert report["emergency_cleanup_attempted"] is True
    assert report["emergency_cleanup_restored"] is True
    assert provider.removed == ["probe"]


def test_verifier_refuses_cleanup_after_concurrent_cart_change(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    provider = FakeProvider(concurrent_after_add_read=True)
    registry = FakeRegistry(provider)

    code, report = verify(allow_reversible_cart_write=True, registry=registry)

    assert code == 1
    assert report["failure_stage"] == "add"
    assert report["emergency_cleanup_attempted"] is True
    assert report["emergency_cleanup_refused"] is True
    assert provider.removed == []
    assert provider._http.items == {"existing": 1, "probe": 1, "concurrent": 1}


def test_verifier_does_not_repeat_an_ambiguous_remove(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    provider = FakeProvider(ambiguous_remove=True)
    registry = FakeRegistry(provider)

    code, report = verify(allow_reversible_cart_write=True, registry=registry)

    assert code == 1
    assert report["failure_stage"] == "remove"
    assert provider.removed == ["probe"]
    assert provider._http.items == {"existing": 1}
