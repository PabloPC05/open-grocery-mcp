"""Built-in supermarket providers."""

from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.gadis import GadisProvider
from open_grocery_mcp.providers.mercadona import MercadonaProvider

__all__ = ["GroceryProvider", "GadisProvider", "MercadonaProvider"]
