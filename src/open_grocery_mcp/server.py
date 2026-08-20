"""MCP tool definitions for Open Grocery."""

from __future__ import annotations

import os
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

from open_grocery_mcp import __version__
from open_grocery_mcp.authenticated_tools import register_authenticated_tools
from open_grocery_mcp.comparison import compare_baskets, parse_basket, price_basket
from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.drafts import DraftCartStore
from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.registry import default_registry
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
) -> dict[str, Any]:
    """Compare one shopping list across stores using normalized product matches."""

    if search_limit < 1 or search_limit > 50:
        raise InvalidRequest("search_limit must be between 1 and 50")
    return compare_baskets(
        _registry,
        items=items,
        stores=stores,
        postal_code=postal_code,
        search_limit=search_limit,
        eco=eco,
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
