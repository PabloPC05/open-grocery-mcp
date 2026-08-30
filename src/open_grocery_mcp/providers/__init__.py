"""Built-in supermarket providers with lazy imports."""

from __future__ import annotations

from typing import Any

from open_grocery_mcp.providers.base import GroceryProvider

__all__ = [
    "GroceryProvider",
    "DiaFullProvider",
    "FroizProvider",
    "FroizFullProvider",
    "GadisProvider",
    "GadisFullProvider",
    "EroskiFullProvider",
    "MercadonaProvider",
    "MercadonaFullProvider",
]


def __getattr__(name: str) -> Any:
    if name == "DiaFullProvider":
        from open_grocery_mcp.providers.dia_full import DiaFullProvider

        return DiaFullProvider
    if name == "FroizProvider":
        from open_grocery_mcp.providers.froiz import FroizProvider

        return FroizProvider
    if name == "FroizFullProvider":
        from open_grocery_mcp.providers.froiz_full import FroizFullProvider

        return FroizFullProvider
    if name == "GadisProvider":
        from open_grocery_mcp.providers.gadis import GadisProvider

        return GadisProvider
    if name == "GadisFullProvider":
        from open_grocery_mcp.providers.gadis_full import GadisFullProvider

        return GadisFullProvider
    if name == "EroskiFullProvider":
        from open_grocery_mcp.providers.eroski_full import EroskiFullProvider

        return EroskiFullProvider
    if name == "MercadonaProvider":
        from open_grocery_mcp.providers.mercadona import MercadonaProvider

        return MercadonaProvider
    if name == "MercadonaFullProvider":
        from open_grocery_mcp.providers.mercadona_full import MercadonaFullProvider

        return MercadonaFullProvider
    raise AttributeError(name)
