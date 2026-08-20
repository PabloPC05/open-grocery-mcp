"""Provider contract implemented by every supermarket adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from open_grocery_mcp.errors import UnsupportedOperation
from open_grocery_mcp.models import Product, StoreInfo


class GroceryProvider(ABC):
    """Read-only catalog interface.

    Cart mutation and checkout deliberately do not belong to this base contract.
    They will be optional capability interfaces so an adapter cannot accidentally
    place an order merely because it can search a catalogue.
    """

    info: StoreInfo

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        """Search a store-specific catalogue."""

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        raise UnsupportedOperation(f"{self.info.label} does not expose product detail yet")

    def categories(
        self,
        *,
        depth: int = 1,
        postal_code: str | None = None,
    ) -> list[dict[str, Any]]:
        raise UnsupportedOperation(f"{self.info.label} does not expose categories yet")

    def close(self) -> None:
        """Release HTTP resources. Providers without resources may ignore this."""
