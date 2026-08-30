"""Carrefour Spain full provider (catalogue-only, no authentication)."""

from __future__ import annotations

from typing import Any

from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.carrefour_catalogue import CarrefourCatalogueProvider


class CarrefourFullProvider(GroceryProvider):
    """Carrefour Spain catalogue provider.
    
    Provides public catalogue search via Empathy API. No authentication,
    cart, or checkout support (out of scope per project requirements).
    
    Cloudflare WAF blocks hosted deployments (Vercel/Lambda). Local MCP
    can use browser session cookies for access (same pattern as Eroski).
    """

    info = StoreInfo(
        key="carrefour",
        label="Carrefour",
        country="ES",
        languages=("es",),
        capabilities=("search", "product"),
        requires_postal_code=False,
        price_scope="Carrefour Spain online catalogue",
        notes=(
            "Public catalogue via Empathy search API. "
            "Cloudflare protects endpoints: hosted MCP blocked, "
            "local MCP can use browser session. "
            "Postal code validated but not required by search API. "
            "Cart, addresses, slots, checkout, and orders not supported."
        ),
    )

    def __init__(self) -> None:
        self._catalogue = CarrefourCatalogueProvider()

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        """Search products in Carrefour catalogue."""
        return self._catalogue.search(
            query, limit=limit, postal_code=postal_code, eco=eco
        )

    def catalogue_contract(self) -> dict[str, Any]:
        """Return catalogue capabilities."""
        return self._catalogue.catalogue_contract()

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        """Get product by ID."""
        return self._catalogue.product(product_id, postal_code=postal_code)

    def close(self) -> None:
        """Close resources."""
        self._catalogue.close()


__all__ = ["CarrefourFullProvider"]
