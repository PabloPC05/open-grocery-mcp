import pytest

from open_grocery_mcp.errors import StoreNotFound
from open_grocery_mcp.registry import ProviderRegistry


def test_builtin_registry_lists_generic_store_metadata() -> None:
    registry = ProviderRegistry()
    stores = registry.list(country="ES")
    assert {store["key"] for store in stores} == {"gadis", "mercadona"}
    assert next(s for s in stores if s["key"] == "mercadona")["requires_postal_code"] is True
    registry.close()


def test_unknown_store_error_lists_valid_keys() -> None:
    registry = ProviderRegistry()
    with pytest.raises(StoreNotFound, match="gadis"):
        registry.get("froiz")
