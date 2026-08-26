from __future__ import annotations

from decimal import Decimal

import pytest

from open_grocery_mcp import server
from open_grocery_mcp.catalogue_search import search_catalogues_expanded
from open_grocery_mcp.errors import InvalidRequest, ProviderError
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry


class SearchProvider(GroceryProvider):
    def __init__(
        self,
        key: str,
        products: dict[str, list[Product]],
        *,
        failing_query: str | None = None,
    ) -> None:
        self.info = StoreInfo(
            key=key,
            label=key.title(),
            country="ES",
            languages=("es",),
            capabilities=("search",),
        )
        self.products = products
        self.failing_query = failing_query
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, limit: int = 10, **_: object) -> list[Product]:
        self.calls.append((query, limit))
        if query == self.failing_query:
            raise ProviderError("catalogue unavailable")
        return self.products.get(query, [])[:limit]


def product(
    store: str,
    product_id: str,
    name: str,
    price: str,
    unit_price: str | None,
    *,
    category: str | None = "Queso gallego",
) -> Product:
    return Product(
        store=store,
        id=product_id,
        name=name,
        price=Decimal(price),
        price_per_unit=Decimal(unit_price) if unit_price else None,
        unit="kg" if unit_price else None,
        category=category,
    )


def test_expanded_search_unions_deduplicates_and_filters_token_boundaries() -> None:
    arzua = product("alpha", "1", "Queso DOP Arzúa-Ulloa", "8", "10")
    ulloa = product("alpha", "2", "Queixo tierno A. Ulloa", "7", "9")
    wine = product(
        "alpha",
        "3",
        "Vino Arzuaga crianza",
        "20",
        "25",
        category="Vino",
    )
    provider = SearchProvider(
        "alpha",
        {
            "queso de arzua": [arzua, wine],
            "queso arzua": [arzua],
            "ulloa": [ulloa, arzua],
        },
    )
    registry = ProviderRegistry(factories={"alpha": lambda: provider})

    result = search_catalogues_expanded(
        registry,
        query="queso de arzua",
        aliases=["ulloa"],
        required_term_groups=[["queso", "queixo"], ["arzua", "ulloa"]],
        sort_by="unit_price",
        auto_equivalences=False,
    )

    store = result["stores"][0]
    assert result["query_variants"] == ["queso de arzua", "queso arzua", "ulloa"]
    assert [row["id"] for row in store["products"]] == ["2", "1"]
    assert store["raw_hits"] == 5
    assert store["unique_before_filter"] == 3
    assert store["rejected_by_filters"] == 1
    assert store["products"][1]["matched_queries"] == [
        "queso de arzua",
        "queso arzua",
        "ulloa",
    ]


def test_expanded_search_uses_semantic_aliases_and_rejects_other_families() -> None:
    arzua = product("alpha", "1", "Queso DOP Arzúa-Ulloa", "8", "10")
    ulloa = product("alpha", "2", "Queixo tierno A. Ulloa", "7", "9")
    wine = product("alpha", "3", "Vino Arzuaga crianza", "20", "25", category="Vino")
    provider = SearchProvider(
        "alpha",
        {
            "queso de arzua": [arzua, wine],
            "queso arzua": [arzua, wine],
            "ulloa": [ulloa],
        },
    )
    registry = ProviderRegistry(factories={"alpha": lambda: provider})

    result = search_catalogues_expanded(registry, query="queso de arzua")

    store = result["stores"][0]
    assert "ulloa" in result["query_variants"]
    assert {row["id"] for row in store["products"]} == {"1", "2"}
    assert all(row["semantic_match"]["verdict"] != "different" for row in store["products"])


def test_expanded_search_reports_saturation_errors_and_partial_evidence() -> None:
    rows = [product("alpha", str(index), f"Queso Arzúa {index}", "2", "10") for index in range(2)]
    provider = SearchProvider(
        "alpha",
        {"queso arzua": rows},
        failing_query="ulloa",
    )
    registry = ProviderRegistry(factories={"alpha": lambda: provider})

    result = search_catalogues_expanded(
        registry,
        query="queso arzua",
        aliases=["ulloa"],
        limit_per_query=2,
    )

    store = result["stores"][0]
    assert result["partial"] is True
    assert store["possibly_saturated_queries"] == ["queso arzua"]
    assert store["errors"] == [{"query": "ulloa", "error": "catalogue unavailable"}]
    assert store["bounded_complete"] is False


def test_expanded_search_validates_bounded_inputs() -> None:
    registry = ProviderRegistry(factories={"alpha": lambda: SearchProvider("alpha", {})})

    with pytest.raises(InvalidRequest, match="limit_per_query"):
        search_catalogues_expanded(registry, query="queso", limit_per_query=501)
    with pytest.raises(InvalidRequest, match="cannot be empty"):
        search_catalogues_expanded(
            registry,
            query="queso",
            required_term_groups=[[]],
        )
    with pytest.raises(InvalidRequest, match="sort_by"):
        search_catalogues_expanded(registry, query="queso", sort_by="unknown")
    with pytest.raises(InvalidRequest, match="at least one store"):
        search_catalogues_expanded(registry, query="queso", stores=[])


def test_expanded_search_distinguishes_filtering_from_result_truncation() -> None:
    rows = [product("alpha", str(index), f"Queso Arzúa {index}", "2", "10") for index in range(3)]
    provider = SearchProvider("alpha", {"queso arzua": rows})
    registry = ProviderRegistry(factories={"alpha": lambda: provider})

    result = search_catalogues_expanded(
        registry,
        query="queso arzua",
        result_limit=2,
    )

    store = result["stores"][0]
    assert store["filtered_before_result_limit"] == 3
    assert store["rejected_by_filters"] == 0
    assert store["result_limit_truncated"] is True
    assert store["count"] == 2


def test_expanded_search_keeps_accented_and_unaccented_provider_queries() -> None:
    accented = product(
        "alpha",
        "1",
        "Atún claro",
        "2",
        "10",
        category="Conservas de pescado",
    )
    unaccented = product(
        "alpha",
        "2",
        "Bonito del norte",
        "3",
        "12",
        category="Conservas de pescado",
    )
    provider = SearchProvider(
        "alpha",
        {
            "atún": [accented],
            "atun": [unaccented],
        },
    )
    registry = ProviderRegistry(factories={"alpha": lambda: provider})

    result = search_catalogues_expanded(registry, query="atún")

    assert result["query_variants"][:2] == ["atún", "atun"]
    assert {item["id"] for item in result["stores"][0]["products"]} == {"1", "2"}


def test_server_exposes_expanded_search(monkeypatch: pytest.MonkeyPatch) -> None:
    item = product("alpha", "1", "Queso Arzúa", "5", "10")
    provider = SearchProvider("alpha", {"queso arzua": [item]})
    monkeypatch.setattr(
        server,
        "_registry",
        ProviderRegistry(factories={"alpha": lambda: provider}),
    )

    result = server.search_products_expanded(
        "queso arzua",
        stores=["alpha"],
        required_term_groups=[["queso"], ["arzua"]],
    )

    assert result["total_count"] == 1
    assert result["stores"][0]["products"][0]["id"] == "1"
