"""Froiz catalogue search adapter.

The current Froiz online shop delegates search to Empathy.co. This adapter calls
that read-only endpoint and normalizes the result. Delivery-area selection,
account sessions, cart mutation and checkout are intentionally outside the
provider's capabilities.
"""

from __future__ import annotations

import os
import unicodedata
from decimal import Decimal
from typing import Any, Mapping

import httpx

from open_grocery_mcp.errors import ProviderError
from open_grocery_mcp.models import Product, StoreInfo, as_decimal
from open_grocery_mcp.providers.base import GroceryProvider

_SEARCH_URL = "https://api.empathy.co/search/v1/query/froiz/search"
_SHOP_BASE = "https://supermercado.froiz.com"


class FroizProvider(GroceryProvider):
    info = StoreInfo(
        key="froiz",
        label="Froiz",
        country="ES",
        languages=("es",),
        capabilities=("search", "compare", "draft_cart"),
        requires_postal_code=False,
        price_scope="Froiz online search catalogue (not yet delivery-area-aware)",
        notes=(
            "Search is read-only. Delivery coverage, store-specific assortment, "
            "product detail, categories and cart operations are not implemented yet."
        ),
    )

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
        instance: str | None = None,
    ) -> None:
        self._instance = instance or os.getenv("OPEN_GROCERY_FROIZ_INSTANCE", "froiz")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "open-grocery-mcp/0.1 "
                    "(+https://github.com/PabloPC05/open-grocery-mcp)"
                ),
            },
        )

    @staticmethod
    def _normalize_query(value: str) -> str:
        decomposed = unicodedata.normalize("NFD", value.strip())
        return "".join(char for char in decomposed if not unicodedata.combining(char))

    @staticmethod
    def _measurement(raw: Mapping[str, Any], price: Decimal) -> tuple[Decimal | None, str | None]:
        unit = str(raw.get("measurementUnit", "")).strip().casefold()
        ratio = as_decimal(raw.get("measurementUnitRatio"))
        if ratio <= 0:
            return None, None
        if unit == "litro":
            return price / ratio, "L"
        if unit == "kilogramo":
            return price / ratio, "kg"
        if unit == "unidad":
            return price / ratio, "u"
        return None, None

    def _product_from_raw(self, raw: Mapping[str, Any]) -> Product | None:
        name = str(raw.get("__name", "")).strip()
        product_id = str(raw.get("id", "")).strip()
        slug = str(raw.get("slug", "")).strip()
        prices = raw.get("__prices", {})
        current = prices.get("current", {}) if isinstance(prices, Mapping) else {}
        price = as_decimal(current.get("value") if isinstance(current, Mapping) else None)
        if not name or price <= 0 or not (product_id or slug):
            return None
        stable_id = product_id or slug
        product_slug = slug or product_id
        price_per_unit, unit = self._measurement(raw, price)
        image_url = str(raw.get("imageUrl", "")).strip()
        return Product(
            store=self.info.key,
            id=stable_id,
            name=name,
            price=price,
            currency="EUR",
            price_per_unit=price_per_unit,
            unit=unit,
            available=True,
            url=f"{_SHOP_BASE}/product/{product_slug}",
            metadata={
                "image_url": image_url or None,
                "measurement_unit": raw.get("measurementUnit"),
                "measurement_unit_ratio": raw.get("measurementUnitRatio"),
                "location_aware": False,
            },
        )

    def _fetch(self, query: str, rows: int) -> list[Product]:
        params = {
            "internal": "true",
            "query": query,
            "origin": "url:external",
            "start": "0",
            "rows": str(rows),
            "instance": self._instance,
            "scope": "desktop",
            "lang": "es",
            "currency": "EUR",
        }
        try:
            response = self._client.get(_SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Froiz search returned HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Could not search Froiz: {exc}") from exc

        catalog = payload.get("catalog", {}) if isinstance(payload, Mapping) else {}
        content = catalog.get("content", []) if isinstance(catalog, Mapping) else []
        products: list[Product] = []
        for raw in content:
            if not isinstance(raw, Mapping):
                continue
            product = self._product_from_raw(raw)
            if product is not None:
                products.append(product)
        return products

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        del postal_code, eco
        normalized = self._normalize_query(query)
        words = normalized.split()
        if not words:
            return []

        requested = max(1, min(limit, 100))
        # The upstream search uses strict AND matching. Try the exact request,
        # then progressively remove trailing qualifiers when no result exists.
        for end in range(len(words), 0, -1):
            products = self._fetch(" ".join(words[:end]), max(10, requested))
            if products:
                return products[:requested]
        return []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
