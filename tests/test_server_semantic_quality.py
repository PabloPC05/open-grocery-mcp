from __future__ import annotations

from decimal import Decimal

import pytest

from open_grocery_mcp import server
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry


class SemanticProvider(GroceryProvider):
    info = StoreInfo("semantic", "Semantic", "ES", ("es",), ("search",))

    def search(self, query: str, **_: object) -> list[Product]:
        return [Product("semantic", "1", query, Decimal("1"), ingredients="trigo")]

    def catalogue_contract(self) -> dict[str, object]:
        return {"pagination": "bounded_test", "exact_total": False}


@pytest.fixture
def semantic_registry(monkeypatch: pytest.MonkeyPatch) -> ProviderRegistry:
    registry = ProviderRegistry({"semantic": SemanticProvider})
    monkeypatch.setattr(server, "_registry", registry)
    return registry


def test_semantic_observability_tools_are_exposed(semantic_registry: ProviderRegistry) -> None:
    del semantic_registry

    status = server.semantic_ontology_status()
    contracts = server.catalogue_contracts(["semantic"])
    corpus = server.audit_semantic_corpus()
    live = server.audit_catalogue_quality(["harina de trigo"], stores=["semantic"])

    assert status["ontology_version"]
    assert status["quality_budget_version"]
    assert contracts["stores"]["semantic"]["pagination"] == "bounded_test"
    assert corpus["failed"] == 0
    assert live["contains_product_or_private_identifiers"] is False


def test_relationship_and_directional_substitution_tools(semantic_registry: ProviderRegistry) -> None:
    del semantic_registry

    identity = server.explain_product_relationship(
        "Harina de trigo",
        "Harina de trigo",
        left_ean="084123",
        right_ean="84123",
    )
    substitution = server.assess_substitution_candidate(
        "lomo de cerdo",
        "Lomo de cerdo filetes",
        intent="para plancha",
    )

    assert identity["relationship"] == "same_sku"
    assert substitution["direction"] == "query_to_candidate"
    assert substitution["intent_evidence"]
