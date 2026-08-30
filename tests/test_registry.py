import pytest

from open_grocery_mcp.errors import StoreNotFound
from open_grocery_mcp.providers.base import (
    CheckoutProvider,
    DeliveryProvider,
    HumanHandoffProvider,
)
from open_grocery_mcp.registry import ProviderRegistry


def test_builtin_registry_lists_generic_store_metadata() -> None:
    registry = ProviderRegistry()
    stores = registry.list(country="ES")
    assert {store["key"] for store in stores} == {
        "carrefour",
        "dia",
        "eroski",
        "froiz",
        "gadis",
        "mercadona",
    }
    assert next(s for s in stores if s["key"] == "mercadona")["requires_postal_code"] is True
    eroski = next(s for s in stores if s["key"] == "eroski")
    froiz = next(s for s in stores if s["key"] == "froiz")
    assert eroski["requires_postal_code"] is False
    assert "product" in eroski["capabilities"]
    assert "delivery" in eroski["capabilities"]
    assert "checkout" not in eroski["capabilities"]
    assert "checkout" not in froiz["capabilities"]
    assert "human_handoff" in eroski["capabilities"]
    assert "human_handoff" in froiz["capabilities"]
    assert isinstance(registry.get("froiz"), DeliveryProvider)
    assert not isinstance(registry.get("froiz"), CheckoutProvider)
    assert isinstance(registry.get("eroski"), DeliveryProvider)
    assert not isinstance(registry.get("eroski"), CheckoutProvider)
    assert all(
        isinstance(registry.get(key), HumanHandoffProvider)
        for key in ("mercadona", "gadis", "froiz", "eroski")
    )
    registry.close()


def test_unknown_store_error_lists_valid_keys() -> None:
    registry = ProviderRegistry()
    with pytest.raises(StoreNotFound, match="carrefour"):
        registry.get("unknown_store_xyz")


def test_empty_registry_stays_empty() -> None:
    registry = ProviderRegistry(factories={})
    assert registry.keys() == ()
