from __future__ import annotations

from decimal import Decimal

from open_grocery_mcp.catalogue_search import (
    clear_catalogue_cache,
    search_catalogues_expanded,
)
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry
from open_grocery_mcp.quality_audit import audit_live_catalogues


class PagedProvider(GroceryProvider):
    info = StoreInfo("paged", "Paged", "ES", ("es",), ("search",))

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, **kwargs: object) -> list[Product]:
        return list(self.search_page(query, **kwargs)["products"])

    def search_page(
        self,
        query: str,
        *,
        page_size: int = 100,
        cursor: str | None = None,
        **_: object,
    ) -> dict[str, object]:
        self.calls += 1
        page = int(cursor or 0)
        all_products = [
            Product("paged", str(index), f"Harina de trigo {index}", Decimal("1"))
            for index in range(3)
        ]
        start = page * page_size
        products = all_products[start : start + page_size]
        has_next = start + page_size < len(all_products)
        return {
            "products": products,
            "next_cursor": str(page + 1) if has_next else None,
            "has_next": has_next,
            "total": len(all_products),
            "pagination": "test_cursor",
        }

    def catalogue_contract(self) -> dict[str, object]:
        return {"pagination": "test_cursor", "exact_total": True}


def test_expanded_search_uses_pages_exact_totals_and_ttl_cache() -> None:
    clear_catalogue_cache()
    provider = PagedProvider()
    registry = ProviderRegistry({"paged": lambda: provider})

    first = search_catalogues_expanded(
        registry,
        query="harina",
        limit_per_query=3,
        cache_ttl_seconds=120,
    )
    calls_after_first = provider.calls
    second = search_catalogues_expanded(
        registry,
        query="harina",
        limit_per_query=3,
        cache_ttl_seconds=120,
    )

    store = first["stores"][0]
    assert store["count"] == 3
    assert store["query_diagnostics"][0]["total"] == 3
    assert store["query_diagnostics"][0]["possibly_saturated"] is False
    assert second["stores"][0]["query_diagnostics"][0]["cache_hit"] is True
    assert calls_after_first == len(first["query_variants"])
    assert provider.calls == calls_after_first


class CategoryProvider(PagedProvider):
    def categories(self, **_: object) -> list[dict[str, object]]:
        return [{"name": "Harinas y levaduras"}]


def test_category_assisted_search_reports_hints() -> None:
    clear_catalogue_cache()
    provider = CategoryProvider()
    registry = ProviderRegistry({"paged": lambda: provider})

    result = search_catalogues_expanded(
        registry,
        query="harina",
        category_search=True,
        cache_ttl_seconds=0,
    )

    assert result["stores"][0]["category_hints_used"] == ["Harinas y levaduras"]


class LargePagedProvider(PagedProvider):
    def __init__(self) -> None:
        super().__init__()
        self.page_sizes: list[int] = []

    def search_page(
        self,
        query: str,
        *,
        page_size: int = 100,
        cursor: str | None = None,
        **_: object,
    ) -> dict[str, object]:
        self.page_sizes.append(page_size)
        page = int(cursor or 0)
        all_products = [
            Product("paged", str(index), f"Harina de trigo {index}", Decimal("1"))
            for index in range(180)
        ]
        start = page * page_size
        products = all_products[start : start + page_size]
        has_next = start + page_size < len(all_products)
        return {
            "products": products,
            "next_cursor": str(page + 1) if has_next else None,
            "has_next": has_next,
            "total": len(all_products),
            "pagination": "test_cursor",
        }


def test_pagination_keeps_page_size_stable_to_avoid_offset_overlap() -> None:
    provider = LargePagedProvider()
    registry = ProviderRegistry({"paged": lambda: provider})

    result = search_catalogues_expanded(
        registry,
        query="harina",
        limit_per_query=150,
        result_limit=500,
        auto_equivalences=False,
        cache_ttl_seconds=0,
    )

    assert result["stores"][0]["count"] == 150
    assert provider.page_sizes == [100, 100]


def test_expansion_provenance_reports_morphology_and_aliases() -> None:
    clear_catalogue_cache()
    registry = ProviderRegistry({"paged": PagedProvider})

    result = search_catalogues_expanded(
        registry,
        query="harina congelada",
        aliases=["farina congelada"],
        cache_ttl_seconds=0,
    )

    sources = {row["source"] for row in result["query_expansions"]}
    assert "original_query" in sources
    assert "morphological_variant" in sources
    assert "explicit_alias" in sources


def test_live_quality_audit_is_aggregated_and_identifier_free() -> None:
    registry = ProviderRegistry({"paged": PagedProvider})

    result = audit_live_catalogues(
        registry,
        queries=["harina"],
        stores=["paged"],
        limit_per_query=3,
    )

    assert result["mode"] == "read_only_public_catalogue_audit"
    assert result["contains_product_or_private_identifiers"] is False
    assert result["totals"]["accepted"] == 3
    assert "products" not in str(result)


def test_provider_can_disable_cache_for_implicit_authenticated_location() -> None:
    class NoCacheProvider(PagedProvider):
        def catalogue_contract(self) -> dict[str, object]:
            return {"cache_safe": False, "pagination": "test_cursor"}

    provider = NoCacheProvider()
    registry = ProviderRegistry({"paged": lambda: provider})
    for _ in range(2):
        search_catalogues_expanded(
            registry,
            query="harina",
            limit_per_query=3,
            auto_equivalences=False,
            cache_ttl_seconds=120,
        )

    assert provider.calls == 2
