from __future__ import annotations

from copy import deepcopy

import pytest

from open_grocery_mcp.errors import ProviderError
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient as RealFroizHTTPClient
from tools import verify_froiz_cart_local as verifier


PRODUCT_ID = "probe-product"


def _raw(
    cart_id: str,
    quantity: int,
    *,
    comment: str = "",
    unit_price: float = 1.20,
) -> dict:
    items = []
    if quantity:
        items.append(
            {
                "product": {"id": PRODUCT_ID, "name": "Leche", "price": unit_price},
                "qty": quantity,
                "unit": "ud",
                "comment": comment,
                "enabled": True,
            }
        )
    return {
        "id": cart_id,
        "items": items,
        "subtotal": round(quantity * unit_price, 2),
        "total": round(quantity * unit_price + 4, 2),
    }


class FakeFroizHTTP:
    def __init__(
        self,
        *,
        active_cart_id: str = "channel-cart",
        create_cart_id: str = "disposable-cart",
        zero_actual_quantity: int | None = None,
        mutate_channel_comment: bool = False,
        actual_unit_price: float = 1.20,
        rebind_channel_on_create: bool = False,
    ) -> None:
        self.active_cart_id = active_cart_id
        self.create_cart_id = create_cart_id
        self.zero_actual_quantity = zero_actual_quantity
        self.mutate_channel_comment = mutate_channel_comment
        self.actual_unit_price = actual_unit_price
        self.rebind_channel_on_create = rebind_channel_on_create
        self.channel = _raw(active_cart_id, 1, unit_price=actual_unit_price)
        self.disposable: dict | None = None
        self.deleted: list[str] = []
        self.raw_reads: list[str] = []
        self.updates: list[tuple[str, int]] = []
        self.closed = False

    def addresses(self) -> list[dict]:
        return [{"id": "address", "is_default": True}]

    def delivery_calendar(self) -> list[dict]:
        return [{"id": "slot", "available": True}]

    def default_postal_code(self) -> str:
        return "28050"

    def store_by_postal_code(self, postal_code: str) -> dict:
        assert postal_code == "28050"
        return {"codEnt": "E1", "codSubent": "S2"}

    def channel_cart_id(self) -> str | None:
        return self.active_cart_id

    def raw_cart(self, cart_id: str) -> dict:
        self.raw_reads.append(cart_id)
        if self.disposable is not None and cart_id == self.create_cart_id:
            return deepcopy(self.disposable)
        if cart_id == self.active_cart_id:
            return deepcopy(self.channel)
        if self.disposable is None or cart_id != self.create_cart_id:
            raise ProviderError("cart no longer exists")
        return deepcopy(self.disposable)

    def processed_cart(self, cart_id: str) -> dict:
        return self.raw_cart(cart_id)

    def create_cart(self, items: list[dict]) -> dict:
        requested = int(items[0]["qty"]) if items else 0
        self.disposable = _raw(
            self.create_cart_id, requested, unit_price=self.actual_unit_price
        )
        if self.rebind_channel_on_create:
            self.active_cart_id = self.create_cart_id
        elif self.active_cart_id is None:
            self.active_cart_id = self.create_cart_id
        # Deliberately return a stale/empty response. The verifier must reread.
        return _raw(self.create_cart_id, 0, unit_price=self.actual_unit_price)

    def update_cart(self, cart_id: str, items: list[dict]) -> dict:
        assert cart_id == self.create_cart_id
        requested = int(items[0]["qty"]) if items else 0
        self.updates.append((cart_id, requested))
        actual = 0 if requested == self.zero_actual_quantity else requested
        self.disposable = _raw(cart_id, actual, unit_price=self.actual_unit_price)
        if self.mutate_channel_comment and requested == 0:
            self.channel["items"][0]["comment"] = "changed-out-of-band"
        # Deliberately return the wrong quantity to catch response-only checks.
        return _raw(cart_id, 0, unit_price=self.actual_unit_price)

    def delete_cart(self, cart_id: str) -> None:
        assert cart_id == self.create_cart_id
        self.deleted.append(cart_id)
        self.disposable = None
        if self.active_cart_id == cart_id:
            self.active_cart_id = None

    @staticmethod
    def normalize_cart(payload: dict) -> dict:
        return RealFroizHTTPClient.normalize_cart(payload)

    def close(self) -> None:
        self.closed = True


def _enable_safe_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "1")
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv(
        "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False
    )


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch, client: FakeFroizHTTP) -> None:
    monkeypatch.setattr(verifier, "FroizHTTPClient", lambda: client)
    monkeypatch.setattr(
        verifier,
        "select_test_product",
        lambda client, store, excluded, max_added_value: {
            "product_id": PRODUCT_ID,
            "name": "Leche",
        },
    )


def test_live_verifier_rereads_every_mutation_and_preserves_active_cart(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    client = FakeFroizHTTP()
    _patch_dependencies(monkeypatch, client)

    code, report = verifier.verify(allow_reversible_cart_write=True)

    assert code == 0
    assert report["ok"] is True
    assert report["channel_cart_untouched"] is True
    assert client.deleted == ["disposable-cart"]
    # empty create + add + qty 2 + qty 1 + remove are reread from GET /raw.
    assert client.raw_reads.count("disposable-cart") >= 5
    assert client.raw_cart("channel-cart") == _raw("channel-cart", 1)
    assert client.closed is True


def test_live_verifier_opens_guarded_checkout_before_disposal(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    client = FakeFroizHTTP(active_cart_id=None)
    _patch_dependencies(monkeypatch, client)

    class ReviewProvider:
        closed = False

        def open_human_review(self, **kwargs):
            assert kwargs["checkout_review"] is True
            assert client.disposable is not None
            return {
                "window_opened": True,
                "network_write_guard": "all_non_get_blocked",
                "review_path_verified": True,
                "non_get_requests_blocked": 3,
            }

        def close(self):
            self.closed = True

    review = ReviewProvider()
    code, report = verifier.verify(
        allow_reversible_cart_write=True,
        open_checkout_review=True,
        review_timeout_seconds=30,
        review_provider_factory=lambda: review,
    )

    assert code == 0
    assert report["checkout_review_reached"] is True
    assert report["all_non_get_blocked"] is True
    assert report["channel_cart_untouched"] is True
    assert client.disposable is None
    assert review.closed is True


def test_live_verifier_rejects_actual_zero_quantity_even_when_put_echoes_success(
    monkeypatch,
) -> None:
    _enable_safe_writes(monkeypatch)
    client = FakeFroizHTTP(zero_actual_quantity=2)
    _patch_dependencies(monkeypatch, client)

    code, report = verifier.verify(allow_reversible_cart_write=True)

    assert code == 1
    assert report["ok"] is False
    assert report["failure_stage"] == "quantity_two"
    assert report["steps"]["quantity_two_verified"] is False
    # The known disposable cart is still cleaned up after the failed readback.
    assert client.deleted == ["disposable-cart"]


def test_live_verifier_enforces_cap_against_authoritative_cart_total(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    # Public search can be stale or differ from the account's live price.  Two
    # units at 3 EUR must fail the hard 5 EUR temporary-value cap.
    client = FakeFroizHTTP(actual_unit_price=3.00)
    _patch_dependencies(monkeypatch, client)

    code, report = verifier.verify(
        allow_reversible_cart_write=True,
        max_added_value=verifier.Decimal("5.00"),
    )

    assert code == 1
    assert report["ok"] is False
    assert report["failure_stage"] == "quantity_two"
    assert client.deleted == ["disposable-cart"]


def test_live_verifier_refuses_to_mutate_or_delete_active_cart_id(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    client = FakeFroizHTTP(create_cart_id="channel-cart")
    _patch_dependencies(monkeypatch, client)

    code, report = verifier.verify(allow_reversible_cart_write=True)

    assert code == 1
    assert report["ok"] is False
    assert report["failure_stage"] == "create"
    assert client.updates == []
    assert client.deleted == []


def test_live_verifier_restores_an_initially_unbound_channel(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    client = FakeFroizHTTP(active_cart_id=None)
    _patch_dependencies(monkeypatch, client)

    code, report = verifier.verify(allow_reversible_cart_write=True)

    assert code == 0
    assert report["ok"] is True
    assert report["started_without_channel_cart"] is True
    assert report["created_cart_became_active"] is True
    assert report["channel_cart_untouched"] is True
    assert client.active_cart_id is None
    assert client.disposable is None
    assert client.deleted == ["disposable-cart"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw["items"].__setitem__(0, {**raw["items"][0], "qty": 99}),
        lambda raw: raw["items"].__setitem__(
            0, {**raw["items"][0], "comment": "concurrent"}
        ),
        lambda raw: raw.__setitem__("total", 999),
    ],
)
def test_unbound_cleanup_rejects_concurrent_probe_state_changes(mutate) -> None:
    raw = _raw("disposable-cart", 1)
    mutate(raw)

    assert verifier._cleanup_matches_probe_cart(raw, PRODUCT_ID) is False


def test_unbound_cleanup_accepts_only_the_exact_probe_state() -> None:
    assert verifier._cleanup_matches_probe_cart(_raw("disposable-cart", 1), PRODUCT_ID)
    assert verifier._cleanup_matches_probe_cart(_raw("disposable-cart", 2), PRODUCT_ID)
    assert verifier._cleanup_matches_probe_cart(_raw("disposable-cart", 0), PRODUCT_ID)


def test_unbound_cleanup_accepts_the_exact_raw_contract_without_enrichment() -> None:
    payload = {
        "id": "disposable-cart",
        "items": [
            {
                "product_id": PRODUCT_ID,
                "qty": 1,
                "unit": "ud",
                "comment": "",
            }
        ],
    }

    assert verifier._cleanup_matches_probe_cart(payload, PRODUCT_ID)


def test_live_verifier_refuses_a_post_that_rebinds_the_active_channel(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    client = FakeFroizHTTP(rebind_channel_on_create=True)
    _patch_dependencies(monkeypatch, client)

    code, report = verifier.verify(allow_reversible_cart_write=True)

    assert code == 1
    assert report["ok"] is False
    assert report["failure_stage"] == "create"
    assert report["write_attempts"] == 1
    assert report["cleanup_required"] is True
    assert report["steps"]["empty_cart_created_verified"] is False
    assert client.updates == []
    assert client.deleted == []


def test_active_cart_fingerprint_detects_non_quantity_mutation(monkeypatch) -> None:
    _enable_safe_writes(monkeypatch)
    client = FakeFroizHTTP(mutate_channel_comment=True)
    _patch_dependencies(monkeypatch, client)

    code, report = verifier.verify(allow_reversible_cart_write=True)

    assert code == 1
    assert report["ok"] is False
    assert report["channel_cart_untouched"] is False
