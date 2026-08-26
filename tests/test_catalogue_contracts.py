from __future__ import annotations

from typing import Any

from open_grocery_mcp.providers.eroski_full import EroskiFullProvider
from open_grocery_mcp.providers.eroski_catalogue import EroskiCatalogueProvider
from open_grocery_mcp.providers.froiz import FroizProvider
from open_grocery_mcp.providers.froiz_full import FroizFullProvider
from open_grocery_mcp.providers.gadis_full import GadisFullProvider
from open_grocery_mcp.providers.mercadona_full import MercadonaFullProvider


class CatalogueStub:
    def search_page(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return {"query": query, **kwargs}

    def catalogue_contract(self) -> dict[str, Any]:
        return {"pagination": "verified", "exact_total": True}


def _composite(cls: type[Any]) -> Any:
    provider = object.__new__(cls)
    provider._catalogue = CatalogueStub()
    return provider


def test_exact_page_contract_is_delegated_by_composite_providers() -> None:
    for cls in (MercadonaFullProvider, GadisFullProvider):
        provider = _composite(cls)
        page = provider.search_page("harina", page_size=25, cursor="2", postal_code="15001")
        assert page["cursor"] == "2"
        assert provider.catalogue_contract()["exact_total"] is True


def test_bounded_provider_contracts_are_explicit() -> None:
    contracts = (
        FroizProvider.catalogue_contract(object()),
        EroskiCatalogueProvider.catalogue_contract(object()),
    )
    for contract in contracts:
        assert contract["exact_total"] is False
        assert contract["hard_limit"]

    for cls in (FroizFullProvider, EroskiFullProvider):
        assert _composite(cls).catalogue_contract()["pagination"] == "verified"
