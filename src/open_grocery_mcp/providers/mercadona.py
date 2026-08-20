"""Mercadona catalogue adapter with postal-code-aware pricing."""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from open_grocery_mcp.errors import CoverageError, LocationRequired, ProviderError
from open_grocery_mcp.models import Product, StoreInfo, as_decimal
from open_grocery_mcp.providers.base import GroceryProvider

_BASE_URL = "https://tienda.mercadona.es"
_DEFAULT_ALGOLIA_APP = "7UZJKL1DJ0"
_DEFAULT_ALGOLIA_KEY = "9d8f2e39e90df472b4f2e559a116fe17"
_POSTAL_RE = re.compile(r"^\d{5}$")


class MercadonaProvider(GroceryProvider):
    info = StoreInfo(
        key="mercadona",
        label="Mercadona",
        country="ES",
        languages=("es",),
        capabilities=("search", "product", "categories", "compare", "draft_cart"),
        requires_postal_code=True,
        price_scope="warehouse serving the supplied Spanish postal code",
        notes=(
            "Pass postal_code for location-correct assortment and prices, or set "
            "OPEN_GROCERY_MERCADONA_WAREHOUSE."
        ),
    )

    def __init__(
        self,
        *,
        warehouse: str | None = None,
        algolia_app: str | None = None,
        algolia_key: str | None = None,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._configured_warehouse = (
            warehouse or os.getenv("OPEN_GROCERY_MERCADONA_WAREHOUSE")
        )
        self._algolia_app = (
            algolia_app
            or os.getenv("OPEN_GROCERY_MERCADONA_ALGOLIA_APP")
            or _DEFAULT_ALGOLIA_APP
        )
        self._algolia_key = (
            algolia_key
            or os.getenv("OPEN_GROCERY_MERCADONA_ALGOLIA_KEY")
            or _DEFAULT_ALGOLIA_KEY
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "open-grocery-mcp/0.1 (+https://github.com/PabloPC05/open-grocery-mcp)",
            },
        )
        self._warehouse_cache: dict[str, str] = {}
        self._cache_lock = threading.Lock()

    @staticmethod
    def _validate_postal_code(postal_code: str) -> str:
        value = postal_code.strip()
        if not _POSTAL_RE.fullmatch(value):
            raise LocationRequired(
                "Mercadona requires a five-digit Spanish postal code, for example '28050'"
            )
        return value

    def resolve_warehouse(self, postal_code: str) -> str:
        """Resolve the warehouse serving a postal code using the storefront call."""

        postal_code = self._validate_postal_code(postal_code)
        with self._cache_lock:
            cached = self._warehouse_cache.get(postal_code)
        if cached:
            return cached
        try:
            response = self._client.post(
                f"{_BASE_URL}/api/postal-codes/actions/change-pc/",
                json={"new_postal_code": postal_code},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CoverageError(
                f"Mercadona rejected postal code {postal_code!r} "
                f"with HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not resolve Mercadona delivery area: {exc}") from exc
        warehouse = response.headers.get("x-customer-wh", "").strip()
        if not warehouse:
            raise CoverageError(
                f"Mercadona did not return an online warehouse for postal code {postal_code!r}"
            )
        with self._cache_lock:
            self._warehouse_cache[postal_code] = warehouse
        return warehouse

    def _warehouse(self, postal_code: str | None) -> str:
        if postal_code:
            return self.resolve_warehouse(postal_code)
        if self._configured_warehouse:
            return self._configured_warehouse.strip()
        raise LocationRequired(
            "Mercadona prices are warehouse-specific; pass postal_code or set "
            "OPEN_GROCERY_MERCADONA_WAREHOUSE"
        )

    @staticmethod
    def _unit(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        if normalized in {"kg", "g"}:
            return "kg"
        if normalized in {"l", "ml", "cl"}:
            return "L"
        if normalized in {"u", "un", "ud", "uds", "unidad", "unitat", "pack"}:
            return "u"
        return None

    def _product_from_raw(self, raw: Mapping[str, Any], warehouse: str) -> Product:
        pricing = raw.get("price_instructions", {})
        if not isinstance(pricing, Mapping):
            pricing = {}
        product_id = str(raw.get("id", "")).strip()
        url = str(raw.get("share_url", "")).strip() or None
        if not url and raw.get("slug") and product_id:
            url = f"{_BASE_URL}/product/{product_id}/{str(raw['slug']).strip('/')}"
        reference = as_decimal(pricing.get("reference_price"))
        return Product(
            store=self.info.key,
            id=product_id,
            name=str(raw.get("display_name", "")).strip(),
            price=as_decimal(pricing.get("unit_price")),
            currency="EUR",
            price_per_unit=reference if reference > 0 else None,
            unit=self._unit(pricing.get("reference_format")),
            brand=str(raw.get("brand", "")).strip() or None,
            available=bool(raw.get("published", True)),
            url=url,
            ean=str(raw.get("ean", "")).strip() or None,
            origin=str(raw.get("origin", "")).strip() or None,
            metadata={"warehouse": warehouse},
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        del eco  # Mercadona's search index has no stable ecological facet.
        query = query.strip()
        if not query:
            return []
        warehouse = self._warehouse(postal_code)
        index = f"products_prod_{warehouse}_es"
        url = f"https://{self._algolia_app}-dsn.algolia.net/1/indexes/{index}/query"
        headers = {
            "X-Algolia-Application-Id": self._algolia_app,
            "X-Algolia-API-Key": self._algolia_key,
            "Content-Type": "application/json",
        }
        try:
            response = self._client.post(
                url,
                headers=headers,
                json={"query": query, "hitsPerPage": max(1, min(limit, 100))},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            hint = " Set OPEN_GROCERY_MERCADONA_ALGOLIA_APP/KEY if the public search credentials rotated."
            raise ProviderError(
                f"Mercadona search returned HTTP {exc.response.status_code}.{hint}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Could not search Mercadona: {exc}") from exc
        hits = payload.get("hits", []) if isinstance(payload, Mapping) else []
        result: list[Product] = []
        for raw in hits:
            if not isinstance(raw, Mapping):
                continue
            product = self._product_from_raw(raw, warehouse)
            if product.id and product.name:
                result.append(product)
        return result

    def _api_json(self, path: str, warehouse: str) -> Any:
        try:
            response = self._client.get(
                f"{_BASE_URL}{path}",
                headers={"x-customer-wh": warehouse},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Mercadona returned HTTP {exc.response.status_code} for {path}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Could not read Mercadona catalogue: {exc}") from exc

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        warehouse = self._warehouse(postal_code)
        payload = self._api_json(
            f"/api/products/{quote(product_id.strip(), safe='')}/",
            warehouse,
        )
        if not isinstance(payload, Mapping) or not payload.get("id"):
            raise ProviderError(f"Mercadona product {product_id!r} was not found")
        return self._product_from_raw(payload, warehouse)

    @classmethod
    def _map_categories(cls, raw_categories: Any, depth: int) -> list[dict[str, Any]]:
        if depth <= 0 or not isinstance(raw_categories, list):
            return []
        result: list[dict[str, Any]] = []
        for raw in raw_categories:
            if not isinstance(raw, Mapping):
                continue
            node: dict[str, Any] = {
                "id": str(raw.get("id", "")),
                "name": str(raw.get("name", "")),
            }
            children = cls._map_categories(raw.get("categories", []), depth - 1)
            if children:
                node["children"] = children
            result.append(node)
        return result

    def categories(
        self,
        *,
        depth: int = 1,
        postal_code: str | None = None,
    ) -> list[dict[str, Any]]:
        warehouse = self._warehouse(postal_code)
        payload = self._api_json("/api/categories/", warehouse)
        raw = payload.get("results", []) if isinstance(payload, Mapping) else []
        return self._map_categories(raw, max(1, depth))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
