"""MCP tool definitions for Open Grocery."""

from __future__ import annotations

from typing import Any

try:  # Current official SDK.
    from mcp.server.mcpserver import MCPServer
except ImportError:  # Compatibility with stable 1.x releases.
    from mcp.server.fastmcp import FastMCP as MCPServer

from open_grocery_mcp import __version__
from open_grocery_mcp.comparison import compare_baskets, parse_basket, price_basket
from open_grocery_mcp.drafts import DraftCartStore
from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.registry import default_registry

_INSTRUCTIONS = """
Open Grocery provides read-only supermarket catalogue search, normalized price
comparison and local cart drafts. It does not log in, mutate a retailer cart,
submit checkout, place an order or authorize a payment. Treat product matching
as approximate: show low-confidence matches and remind the user that shipping,
minimum-order rules and account-specific promotions are excluded.
""".strip()


def _new_server() -> Any:
    try:
        return MCPServer(
            name="open-grocery-mcp",
            title="Open Grocery MCP",
            description="Compare supermarket catalogues and prepare reviewable cart drafts.",
            instructions=_INSTRUCTIONS,
            version=__version__,
        )
    except TypeError:
        # Older FastMCP constructors accept only a subset of this metadata.
        return MCPServer("Open Grocery MCP", instructions=_INSTRUCTIONS)


mcp = _new_server()
_registry = default_registry()
_drafts = DraftCartStore()


@mcp.tool()
def health() -> dict[str, Any]:
    """Return server version, safety mode and the currently registered stores."""

    return {
        "name": "open-grocery-mcp",
        "version": __version__,
        "mode": "read_only_catalogue_and_local_drafts",
        "can_place_orders": False,
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
    """Search one supermarket catalogue.

    ``postal_code`` is required for stores whose assortment and prices depend on
    delivery location, notably Mercadona. This tool never adds products to a cart.
    """

    if not query.strip():
        raise InvalidRequest("query cannot be empty")
    if limit < 1 or limit > 100:
        raise InvalidRequest("limit must be between 1 and 100")
    provider = _registry.get(store)
    products = provider.search(
        query,
        limit=limit,
        postal_code=postal_code,
        eco=eco,
    )
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
    """Get normalized product detail by the retailer's product identifier."""

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
    """Return a supermarket's category tree to the requested depth."""

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
    """Compare the same shopping list across stores.

    Each item can be a string or an object such as
    ``{"query": "leche entera 1 L", "quantity": 2}``. Results are normalized
    matches, not guaranteed identical SKUs. Shipping and personalized discounts
    are excluded.
    """

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
    """Prepare a local, reviewable cart draft for one store.

    This resolves product IDs and estimates the subtotal. It does **not** modify
    the retailer's website, submit checkout or place an order.
    """

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
