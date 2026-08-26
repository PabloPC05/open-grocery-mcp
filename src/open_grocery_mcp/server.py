"""MCP tool definitions for Open Grocery."""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

from open_grocery_mcp import __version__
from open_grocery_mcp.authenticated_tools import register_authenticated_tools
from open_grocery_mcp.basket_optimization import optimize_semantic_basket
from open_grocery_mcp.catalogue_search import (
    audit_catalogue_geography,
    search_catalogues_expanded,
)
from open_grocery_mcp.comparison import compare_baskets, parse_basket, price_basket
from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.drafts import DraftCartStore
from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.equivalence import analyze_product_text, compare_profiles
from open_grocery_mcp.models import as_decimal
from open_grocery_mcp.offer_evaluation import evaluate_offer_value
from open_grocery_mcp.quality_audit import audit_corpus, audit_live_catalogues
from open_grocery_mcp.registry import default_registry
from open_grocery_mcp.semantic_quality import (
    ONTOLOGY_VERSION,
    assess_substitution,
    ontology_info,
    relationship,
)
from open_grocery_mcp.value_comparison import (
    compare_alternative_value,
    search_offer_products,
)
from open_grocery_mcp.workflows import RetailerWorkflowService

_INSTRUCTIONS = """
Open Grocery provides supermarket catalogue search, normalized price comparison,
local cart drafts and optional authenticated retailer operations. Every retailer
write is two-phase: call a prepare tool, show its complete summary to the user,
and call the corresponding commit tool only after the user explicitly provides
the exact confirmation phrase. Never infer confirmation. Order submission is
disabled by default and must remain a separate final action. Product matching is
approximate; surface low-confidence matches and excluded delivery/promotional
costs before asking for approval.
""".strip()


def _new_server() -> Any:
    try:
        return MCPServer(
            name="open-grocery-mcp",
            title="Open Grocery MCP",
            description="Compare supermarkets and safely prepare or execute grocery carts.",
            instructions=_INSTRUCTIONS,
            version=__version__,
        )
    except TypeError:
        return MCPServer("Open Grocery MCP", instructions=_INSTRUCTIONS)


mcp = _new_server()
_registry = default_registry()
_drafts = DraftCartStore()
_confirmations = ConfirmationStore(ttl_seconds=300)
_workflows = RetailerWorkflowService(_registry, _drafts, _confirmations)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


@mcp.tool()
def health() -> dict[str, Any]:
    """Return server version, safety mode and registered stores."""

    writes_enabled = _enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES")
    order_enabled = _enabled("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION")
    browser_order_enabled = _enabled("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION")
    approval_configured = len(os.getenv("OPEN_GROCERY_ORDER_APPROVAL_CODE", "")) >= 6
    return {
        "name": "open-grocery-mcp",
        "version": __version__,
        "ontology_version": ONTOLOGY_VERSION,
        "mode": "catalogue_comparison_and_two_phase_retailer_actions",
        "retailer_writes_enabled": writes_enabled,
        "order_submission_enabled": order_enabled,
        "browser_order_submission_enabled": browser_order_enabled,
        "can_place_api_orders": writes_enabled and order_enabled and approval_configured,
        "can_place_browser_orders": (
            writes_enabled and order_enabled and browser_order_enabled and approval_configured
        ),
        "order_approval_code_configured": approval_configured,
        "confirmation_ttl_seconds": 300,
        "stores": list(_registry.keys()),
    }


@mcp.tool()
def stores(country: str | None = None) -> list[dict[str, object]]:
    """List supported stores, languages, location requirements and capabilities."""

    return _registry.list(country=country)


@mcp.tool()
def get_delivery_coverage(store: str, postal_code: str) -> dict[str, Any]:
    """Return a store's public delivery fee, minimum and serving assortment.

    Only providers with a verified public coverage contract expose this tool.
    Account-specific checkout data is never inferred here.
    """

    provider = _registry.get(store)
    coverage = getattr(provider, "delivery_coverage", None)
    if "coverage" not in provider.info.capabilities or not callable(coverage):
        raise InvalidRequest(
            f"{provider.info.label} does not expose a verified public delivery policy"
        )
    result = coverage(postal_code)
    return {
        "store": provider.info.key,
        "label": provider.info.label,
        **dict(result),
    }


@mcp.tool()
def search_products(
    store: str,
    query: str,
    limit: int = 10,
    postal_code: str | None = None,
    eco: bool = False,
) -> dict[str, Any]:
    """Search one supermarket catalogue without changing a cart."""

    if not query.strip():
        raise InvalidRequest("query cannot be empty")
    if limit < 1 or limit > 100:
        raise InvalidRequest("limit must be between 1 and 100")
    provider = _registry.get(store)
    products = provider.search(query, limit=limit, postal_code=postal_code, eco=eco)
    return {
        "store": provider.info.key,
        "query": query,
        "postal_code": postal_code,
        "count": len(products),
        "products": [product.to_dict() for product in products],
    }


@mcp.tool()
def search_products_expanded(
    query: str,
    stores: list[str] | None = None,
    aliases: list[str] | None = None,
    required_term_groups: list[list[str]] | None = None,
    excluded_terms: list[str] | None = None,
    postal_code: str | None = None,
    eco: bool = False,
    limit_per_query: int = 100,
    result_limit: int = 500,
    sort_by: str = "relevance",
    auto_equivalences: bool = True,
    cache_ttl_seconds: int = 120,
    category_search: bool = False,
) -> dict[str, Any]:
    """Search several query variants and stores with coverage diagnostics.

    Use this instead of a single ``search_products`` call before claiming that
    a result is the cheapest or that every relevant product was considered.
    ``required_term_groups`` uses AND between groups and OR within each group;
    for example ``[["queso", "queixo"], ["arzua", "ulloa"]]``.
    """

    return search_catalogues_expanded(
        _registry,
        query=query,
        stores=stores,
        aliases=aliases,
        required_term_groups=required_term_groups,
        excluded_terms=excluded_terms,
        postal_code=postal_code,
        eco=eco,
        limit_per_query=limit_per_query,
        result_limit=result_limit,
        sort_by=sort_by,
        auto_equivalences=auto_equivalences,
        cache_ttl_seconds=cache_ttl_seconds,
        category_search=category_search,
    )


@mcp.tool()
def semantic_ontology_status() -> dict[str, Any]:
    """Return ontology, alias and quality-budget versions and policies."""

    return ontology_info()


@mcp.tool()
def catalogue_contracts(stores: list[str] | None = None) -> dict[str, Any]:
    """Describe verified pagination, total, category and geography contracts."""

    keys = stores or list(_registry.keys())
    return {
        "stores": {
            key: _registry.get(key).catalogue_contract()
            for key in keys
        }
    }


@mcp.tool()
def compare_catalogue_regions(
    query: str,
    postal_codes: list[str],
    stores: list[str] | None = None,
    limit_per_query: int = 50,
) -> dict[str, Any]:
    """Compare bounded public assortments across representative postal codes."""

    return audit_catalogue_geography(
        _registry,
        query=query,
        postal_codes=postal_codes,
        stores=stores,
        limit_per_query=limit_per_query,
    )


@mcp.tool()
def audit_semantic_corpus() -> dict[str, Any]:
    """Run the local anonymized semantic regression corpus."""

    return audit_corpus()


@mcp.tool()
def audit_catalogue_quality(
    queries: list[str],
    stores: list[str] | None = None,
    postal_code: str | None = None,
    limit_per_query: int = 50,
) -> dict[str, Any]:
    """Audit public results, semantic rejections, uncertainty and saturation."""

    return audit_live_catalogues(
        _registry,
        queries=queries,
        stores=stores,
        postal_code=postal_code,
        limit_per_query=limit_per_query,
    )


@mcp.tool()
def assess_substitution_candidate(
    query: str,
    candidate_name: str,
    store: str = "unknown",
    candidate_id: str = "candidate",
    brand: str | None = None,
    category: str | None = None,
    ingredients: str | None = None,
    intent: str | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess one directional substitution under explicit intent and restrictions."""

    from open_grocery_mcp.models import Product

    candidate = Product(
        store=store,
        id=candidate_id,
        name=candidate_name,
        price=Decimal("0"),
        brand=brand,
        category=category,
        ingredients=ingredients,
    )
    return assess_substitution(
        query,
        candidate,
        intent=intent,
        constraints=constraints,
    )


@mcp.tool()
def explain_product_relationship(
    left_name: str,
    right_name: str,
    left_store: str = "left",
    right_store: str = "right",
    left_id: str = "left",
    right_id: str = "right",
    left_ean: str | None = None,
    right_ean: str | None = None,
) -> dict[str, Any]:
    """Separate same-SKU identity, direct equivalence and possible substitution."""

    from open_grocery_mcp.models import Product

    left = Product(
        store=left_store,
        id=left_id,
        name=left_name,
        price=Decimal("0"),
        ean=left_ean,
    )
    right = Product(
        store=right_store,
        id=right_id,
        name=right_name,
        price=Decimal("0"),
        ean=right_ean,
    )
    return relationship(left, right)


@mcp.tool()
def optimize_basket_combination(
    items: list[str | dict[str, Any]],
    stores: list[str] | None = None,
    postal_code: str | None = None,
    constraints: dict[str, Any] | None = None,
    search_limit: int = 20,
    maximum_stores: int = 4,
    review_penalty_percent: float = 5,
) -> dict[str, Any]:
    """Find a semantic multi-store basket including public delivery fees."""

    return optimize_semantic_basket(
        _registry,
        items=items,
        stores=stores,
        postal_code=postal_code,
        constraints=constraints,
        search_limit=search_limit,
        maximum_stores=maximum_stores,
        review_penalty_percent=review_penalty_percent,
    )


@mcp.tool()
def explain_product_equivalence(
    left_name: str,
    right_name: str,
    left_category: str | None = None,
    right_category: str | None = None,
) -> dict[str, Any]:
    """Explain whether two product descriptions are comparable or conflict."""

    if not left_name.strip() or not right_name.strip():
        raise InvalidRequest("both product names are required")
    left = analyze_product_text(left_name, left_category)
    right = analyze_product_text(right_name, right_category)
    return {
        **compare_profiles(left, right),
        "left_profile": left.to_dict(),
        "right_profile": right.to_dict(),
    }


def _positive_number(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise InvalidRequest(f"{name} must be a positive finite number")
    parsed = as_decimal(value)
    if not parsed.is_finite() or parsed <= 0:
        raise InvalidRequest(f"{name} must be a positive finite number")
    return parsed


def _percentage(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise InvalidRequest(f"{name} must be between 0 and 100")
    try:
        parsed = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise InvalidRequest(f"{name} must be between 0 and 100") from None
    if not parsed.is_finite() or parsed < 0 or parsed > 100:
        raise InvalidRequest(f"{name} must be between 0 and 100")
    return parsed


@mcp.tool()
def search_offers(
    store: str,
    query: str,
    quantity: float = 1,
    limit: int = 20,
    postal_code: str | None = None,
    eco: bool = False,
    include_loyalty: bool = False,
) -> dict[str, Any]:
    """Find explicit retailer offers and calculate their quantity-aware price.

    Loyalty prices are excluded unless ``include_loyalty`` is explicitly true.
    Personal coupons and rules lacking enough numeric evidence remain descriptive.
    """

    if limit < 1 or limit > 100:
        raise InvalidRequest("limit must be between 1 and 100")
    provider = _registry.get(store)
    return search_offer_products(
        provider,
        query=query,
        quantity=_positive_number(quantity, "quantity"),
        limit=limit,
        postal_code=postal_code,
        eco=eco,
        include_loyalty=include_loyalty,
    )


@mcp.tool()
def filter_worthwhile_offers(
    store: str,
    query: str,
    quantity: float = 1,
    limit: int = 50,
    postal_code: str | None = None,
    eco: bool = False,
    include_loyalty: bool = False,
    minimum_similarity: float = 0.45,
    maximum_size_ratio: float = 3,
    minimum_advantage_percent: float = 0,
    auto_promotion_quantity: bool = True,
) -> dict[str, Any]:
    """Keep offers that beat the cheapest sufficiently similar product.

    Comparison uses the same €/kg, €/L or €/item basis and includes other
    brands and recognized retailer private labels. Quantity promotions can be
    evaluated automatically at their minimum required quantity.
    """

    if limit < 2 or limit > 100:
        raise InvalidRequest("limit must be between 2 and 100")
    minimum_advantage = _percentage(
        minimum_advantage_percent,
        "minimum_advantage_percent",
    )
    return evaluate_offer_value(
        _registry.get(store),
        query=query,
        quantity=_positive_number(quantity, "quantity"),
        limit=limit,
        postal_code=postal_code,
        eco=eco,
        include_loyalty=include_loyalty,
        minimum_similarity=minimum_similarity,
        maximum_size_ratio=maximum_size_ratio,
        minimum_advantage_percent=minimum_advantage,
        auto_promotion_quantity=auto_promotion_quantity,
    )


@mcp.tool()
def get_product(
    store: str,
    product_id: str,
    postal_code: str | None = None,
) -> dict[str, Any]:
    """Get normalized product detail by retailer product identifier."""

    if not product_id.strip():
        raise InvalidRequest("product_id cannot be empty")
    provider = _registry.get(store)
    return provider.product(product_id, postal_code=postal_code).to_dict()


@mcp.tool()
def list_categories(
    store: str,
    depth: int = 1,
    postal_code: str | None = None,
) -> dict[str, Any]:
    """Return a supermarket category tree."""

    if depth < 1 or depth > 5:
        raise InvalidRequest("depth must be between 1 and 5")
    provider = _registry.get(store)
    categories = provider.categories(depth=depth, postal_code=postal_code)
    return {
        "store": provider.info.key,
        "postal_code": postal_code,
        "depth": depth,
        "categories": categories,
    }


@mcp.tool()
def compare_basket(
    items: list[str | dict[str, Any]],
    stores: list[str] | None = None,
    postal_code: str | None = None,
    search_limit: int = 10,
    eco: bool = False,
    include_loyalty: bool = False,
) -> dict[str, Any]:
    """Compare a list using quantity-aware offers and normalized matches."""

    if search_limit < 1 or search_limit > 50:
        raise InvalidRequest("search_limit must be between 1 and 50")
    return compare_baskets(
        _registry,
        items=items,
        stores=stores,
        postal_code=postal_code,
        search_limit=search_limit,
        eco=eco,
        include_loyalty=include_loyalty,
    )


@mcp.tool()
def compare_alternatives(
    alternatives: list[str],
    stores: list[str] | None = None,
    postal_code: str | None = None,
    quantity: float = 1,
    nutrient: str | None = None,
    target_nutrient_grams: float = 10,
    search_limit: int = 10,
    eco: bool = False,
    include_loyalty: bool = False,
) -> dict[str, Any]:
    """Rank substitutable foods by effective price and optional nutrient value.

    Nutrient ranking is emitted only from an explicit retailer declaration per
    100 g/ml; it is a price/value comparison, not dietary advice.
    """

    if search_limit < 1 or search_limit > 50:
        raise InvalidRequest("search_limit must be between 1 and 50")
    return compare_alternative_value(
        _registry,
        alternatives=alternatives,
        stores=stores,
        postal_code=postal_code,
        quantity=_positive_number(quantity, "quantity"),
        nutrient=nutrient,
        target_nutrient_grams=_positive_number(
            target_nutrient_grams,
            "target_nutrient_grams",
        ),
        search_limit=search_limit,
        eco=eco,
        include_loyalty=include_loyalty,
    )


@mcp.tool()
def prepare_cart(
    store: str,
    items: list[str | dict[str, Any]],
    postal_code: str | None = None,
    search_limit: int = 10,
    eco: bool = False,
) -> dict[str, Any]:
    """Create a local cart draft; this does not touch the retailer."""

    if search_limit < 1 or search_limit > 50:
        raise InvalidRequest("search_limit must be between 1 and 50")
    parsed = parse_basket(items)
    provider = _registry.get(store)
    result = price_basket(
        provider,
        parsed,
        postal_code=postal_code,
        search_limit=search_limit,
        eco=eco,
    )
    if result["items_found"] == 0:
        raise InvalidRequest("no products could be matched; no draft was created")
    return _drafts.create(result)


@mcp.tool()
def get_cart_draft(draft_id: str) -> dict[str, Any]:
    """Read an unexpired local cart draft by ID."""

    return _drafts.get(draft_id)


@mcp.tool()
def delete_cart_draft(draft_id: str) -> dict[str, Any]:
    """Delete a local cart draft. No retailer cart is affected."""

    return _drafts.delete(draft_id)


register_authenticated_tools(mcp, _workflows)
