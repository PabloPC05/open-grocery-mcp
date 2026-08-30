"""Carrefour Spain full provider (catalogue + session login, no cart/orders)."""

from __future__ import annotations

from typing import Any

from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import CARREFOUR_BROWSER_CONFIG
from open_grocery_mcp.providers.carrefour_catalogue import CarrefourCatalogueProvider


class CarrefourFullProvider(GroceryProvider):
    """Carrefour Spain catalogue provider with session login.
    
    Provides public catalogue search via Empathy API. Browser session login
    available to save cookies for Cloudflare-protected catalogue access.
    
    Cart, checkout, and orders are out of scope per project requirements.
    
    Cloudflare WAF blocks hosted deployments (Vercel/Lambda). Local MCP
    can use browser session cookies for access (same pattern as Eroski).
    """

    info = StoreInfo(
        key="carrefour",
        label="Carrefour",
        country="ES",
        languages=("es",),
        capabilities=("search", "product", "login", "account"),
        requires_postal_code=False,
        price_scope="Carrefour Spain online catalogue",
        notes=(
            "Public catalogue via Empathy search API. "
            "Cloudflare protects endpoints: hosted MCP blocked, "
            "local MCP can use browser session via login_carrefour. "
            "Postal code validated but not required by search API. "
            "Cart, addresses, slots, checkout, and orders not supported."
        ),
    )

    def __init__(self) -> None:
        self._catalogue = CarrefourCatalogueProvider()
        self._account = BrowserAccountClient(CARREFOUR_BROWSER_CONFIG)

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

    def account_status(self) -> dict[str, Any]:
        """Return browser session status."""
        return self._account.status()

    def import_browser_session(self, storage_state_path: str) -> dict[str, Any]:
        """Import browser session from storage_state.json file."""
        result = self._account.import_storage_state(storage_state_path)
        return {**result, **self.account_status()}

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        """Open browser for manual Carrefour login and save session."""
        result = self._account.login_with_browser(timeout_seconds=timeout_seconds)
        return {**result, **self.account_status()}

    def clear_session(self) -> dict[str, Any]:
        """Clear stored browser session."""
        state_path = self._account.state_path
        if state_path.exists():
            try:
                state_path.unlink()
                return {
                    "store": "carrefour",
                    "session_cleared": True,
                    "state_path": str(state_path),
                }
            except OSError as exc:
                return {
                    "store": "carrefour",
                    "session_cleared": False,
                    "error": str(exc),
                    "state_path": str(state_path),
                }
        else:
            return {
                "store": "carrefour",
                "session_cleared": False,
                "message": "No session to clear",
                "state_path": str(state_path),
            }

    def close(self) -> None:
        """Close resources."""
        self._catalogue.close()
        self._account.close()


__all__ = ["CarrefourFullProvider"]
