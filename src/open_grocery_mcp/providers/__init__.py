"""Built-in supermarket providers."""

from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.froiz import FroizProvider
from open_grocery_mcp.providers.gadis import GadisProvider
from open_grocery_mcp.providers.mercadona import MercadonaProvider

__all__ = ["GroceryProvider", "FroizProvider", "GadisProvider", "MercadonaProvider"]
