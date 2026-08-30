"""Complete Día provider (catalogue only, no cart/checkout)."""

from __future__ import annotations

from typing import Any

from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.dia_catalogue import DiaCatalogueProvider


class DiaFullProvider(GroceryProvider):
    """Full Día provider exposing public catalogue."""

    def __init__(self) -> None:
        self._catalogue = DiaCatalogueProvider()

    @property
    def info(self) -> StoreInfo:
        return StoreInfo(
            key="dia",
            label="Día",
            country="ES",
            languages=("es",),
            capabilities=("catalogue",),
            requires_postal_code=False,
            price_scope="public HTML catalogue",
            notes=(
                "Public catalogue via HTML scraping. "
                "May be blocked by anti-bot protection from serverless IPs; "
                "use local MCP with login_dia or import_browser_session if blocked."
            ),
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        **kwargs: Any,
    ) -> list[Product]:
        return self._catalogue.search(query, limit=limit, postal_code=postal_code)

    def product(
        self, product_id: str, *, postal_code: str | None = None, **kwargs: Any
    ) -> Product:
        return self._catalogue.product(product_id, postal_code=postal_code)

    def catalogue_contract(self) -> dict[str, Any]:
        return self._catalogue.catalogue_contract()

    def close(self) -> None:
        self._catalogue.close()


__all__ = ["DiaFullProvider"]
