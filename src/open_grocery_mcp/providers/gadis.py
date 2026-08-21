"""Gadis catalogue adapter with location-aware public HTTP reads.

Gadisline exposes JSON microservices used by its own storefront. This adapter
uses those public catalogue/store calls instead of scraping rendered HTML. It
is read-only: no account, cart, checkout or payment endpoint is called.
"""

from __future__ import annotations

import html
import os
import re
import threading
from dataclasses import replace
from typing import Any, Iterable, Mapping
from urllib.parse import quote

import httpx

from open_grocery_mcp.errors import CoverageError, LocationRequired, ProviderError
from open_grocery_mcp.models import Product, StoreInfo, as_decimal
from open_grocery_mcp.providers.base import GroceryProvider

_SITE_BASE = "https://site.gadisline.com/api/v3"
_CATALOG_BASE = "https://catalog.gadisline.com/api/v3"
_STORE_BASE = "https://store.gadisline.com/api/v3"
_SHOP_BASE = "https://www.gadisline.com"
_DOMAIN = "www.gadisline.com"
_ECO_PROPERTY = "36"
_POSTAL_RE = re.compile(r"^\d{5}$")


class GadisProvider(GroceryProvider):
    info = StoreInfo(
        key="gadis",
        label="Gadis",
        country="ES",
        languages=("es", "gl"),
        capabilities=(
            "search",
            "product",
            "categories",
            "coverage",
            "compare",
            "draft_cart",
        ),
        requires_postal_code=False,
        price_scope=(
            "Gadis assortment serving the supplied Spanish postal code, or the "
            "storefront default/environment override when no location is supplied"
        ),
        notes=(
            "Pass postal_code for location-correct assortment and prices. "
            "OPEN_GROCERY_GADIS_STORE remains available as a fixed override."
        ),
    )

    def __init__(
        self,
        *,
        language: str = "es",
        store_id: str | None = None,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.language = "gl" if language.lower().strip() == "gl" else "es"
        self._configured_store_id = (
            store_id
            or os.getenv("OPEN_GROCERY_GADIS_STORE")
            or os.getenv("GROCERY_GADIS_STORE")
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "open-grocery-mcp/0.4 "
                    "(+https://github.com/PabloPC05/open-grocery-mcp)"
                ),
            },
        )
        self._site_id: str | None = None
        self._store_id: str | None = None
        self._bootstrap_lock = threading.Lock()
        self._coverage_lock = threading.Lock()
        self._coverage_by_postal_code: dict[str, dict[str, Any]] | None = None

    @property
    def store_id(self) -> str | None:
        return self._store_id

    def _bootstrap(self) -> tuple[str, str]:
        if self._site_id and self._store_id:
            return self._site_id, self._store_id
        with self._bootstrap_lock:
            if self._site_id and self._store_id:
                return self._site_id, self._store_id
            payload = self._json(
                "GET",
                f"{_SITE_BASE}/sites",
                params={"domain": _DOMAIN},
                include_context=False,
            )
            elements = payload.get("elements", []) if isinstance(payload, Mapping) else []
            if not elements:
                raise ProviderError("Gadis site lookup returned no storefront")
            first = elements[0]
            site_id = str(first.get("id", "")).strip()
            store_id = str(
                self._configured_store_id
                or first.get("default_assortment_store", "")
            ).strip()
            if not site_id or not store_id:
                raise ProviderError(
                    "Gadis did not return a usable site/store identifier"
                )
            self._site_id, self._store_id = site_id, store_id
            return site_id, store_id

    def _json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        include_context: bool = True,
        context_store_id: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {"accept-language": self.language.upper()}
        if include_context:
            site_id, default_store_id = self._bootstrap()
            headers.update(
                {
                    "site-id": site_id,
                    "store-id": context_store_id or default_store_id,
                }
            )
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Gadis returned HTTP {exc.response.status_code} for "
                f"{exc.request.url}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Could not read Gadis catalogue: {exc}") from exc

    @staticmethod
    def _validate_postal_code(postal_code: str) -> str:
        value = postal_code.strip()
        if not _POSTAL_RE.fullmatch(value):
            raise LocationRequired(
                "Gadis requires a five-digit Spanish postal code, for example '28050'"
            )
        return value

    def _load_delivery_coverage(self) -> dict[str, dict[str, Any]]:
        if self._coverage_by_postal_code is not None:
            return self._coverage_by_postal_code
        with self._coverage_lock:
            if self._coverage_by_postal_code is not None:
                return self._coverage_by_postal_code
            payload = self._json(
                "GET",
                f"{_STORE_BASE}/stores/postal-codes/delivery",
            )
            elements = payload.get("elements", []) if isinstance(payload, Mapping) else []
            coverage: dict[str, dict[str, Any]] = {}
            for raw in elements if isinstance(elements, list) else []:
                if not isinstance(raw, Mapping):
                    continue
                postal_code = str(raw.get("postal_code", "")).strip()
                store_id = str(raw.get("store_id", "")).strip()
                if not _POSTAL_RE.fullmatch(postal_code) or not store_id:
                    continue
                coverage[postal_code] = {
                    "store_id": store_id,
                    "postal_code": postal_code,
                    "shipping_costs": float(as_decimal(raw.get("shipping_costs"))),
                    "minimum_order_quantity": float(
                        as_decimal(raw.get("minimum_order_quantity"))
                    ),
                    "minimum_shipping_free": float(
                        as_decimal(raw.get("minimum_shipping_free"))
                    ),
                }
            if not coverage:
                raise ProviderError(
                    "Gadis delivery coverage endpoint returned no usable postal codes"
                )
            self._coverage_by_postal_code = coverage
            return coverage

    def delivery_coverage(self, postal_code: str) -> dict[str, Any]:
        """Return public Gadis delivery/store information for a postal code."""

        value = self._validate_postal_code(postal_code)
        coverage = self._load_delivery_coverage().get(value)
        if coverage is None:
            raise CoverageError(
                f"Gadis did not report online delivery coverage for postal code {value!r}"
            )
        return dict(coverage)

    def _context_store(self, postal_code: str | None) -> str | None:
        if postal_code:
            return str(self.delivery_coverage(postal_code)["store_id"])
        return None

    @staticmethod
    def _translated(value: Any, language: str) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            for item in value:
                if (
                    isinstance(item, Mapping)
                    and str(item.get("language", "")).lower() == language
                ):
                    return str(item.get("value", "")).strip()
            for item in value:
                if isinstance(item, Mapping) and item.get("value"):
                    return str(item["value"]).strip()
        return ""

    @classmethod
    def _unit(cls, suffix: Any, language: str) -> str | None:
        text = cls._translated(suffix, language).lower()
        if "kilo" in text:
            return "kg"
        if "litro" in text:
            return "L"
        if "unidad" in text or "unidade" in text:
            return "u"
        return None

    @staticmethod
    def _deepest_category(categories: Any, language: str) -> str | None:
        if not isinstance(categories, list):
            return None
        best_level = -1
        best_name: str | None = None
        for category in categories:
            if not isinstance(category, Mapping):
                continue
            try:
                level = int(category.get("level", 0))
            except (TypeError, ValueError):
                level = 0
            name = GadisProvider._translated(category.get("name"), language)
            if not name:
                name = GadisProvider._translated(
                    category.get("descriptions_translate"), language
                )
            if name and level >= best_level:
                best_level, best_name = level, name
        return best_name

    @staticmethod
    def _is_eco(raw: Mapping[str, Any]) -> bool:
        properties = raw.get("properties", [])
        return any(
            isinstance(item, Mapping)
            and str(item.get("property_code", "")) == _ECO_PROPERTY
            for item in properties
        )

    def _product_from_raw(
        self,
        raw: Mapping[str, Any],
        *,
        context_store_id: str | None = None,
    ) -> Product:
        product_id = str(raw.get("id", "")).strip()
        name = self._translated(raw.get("commercial_description"), self.language)
        slug = str(raw.get("slug", "")).strip()
        if slug:
            url = slug if slug.startswith("http") else f"{_SHOP_BASE}/{slug.lstrip('/')}"
        else:
            url = None
        price_per_unit = as_decimal(raw.get("price_kilo_litre"))
        return Product(
            store=self.info.key,
            id=product_id,
            name=name,
            price=as_decimal(raw.get("price")),
            price_per_unit=price_per_unit if price_per_unit > 0 else None,
            unit=self._unit(raw.get("price_kilo_litre_suffix"), self.language),
            currency="EUR",
            brand=str(raw.get("brand_description", "")).strip() or None,
            category=self._deepest_category(raw.get("categories"), self.language),
            available=True,
            url=url,
            metadata={
                "eco": self._is_eco(raw),
                "store_id": context_store_id or self._store_id,
                "product_code": raw.get("product_code"),
            },
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        query = query.strip()
        if not query:
            return []
        context_store_id = self._context_store(postal_code)
        requested = max(1, min(limit, 100))
        rows = min(100, requested * 4 if eco else requested)
        payload = self._json(
            "POST",
            f"{_CATALOG_BASE}/catalog/products/search",
            params={
                "page_number": "0",
                "rows_per_page": str(rows),
                "keep_request": "true",
            },
            body={"search_term": query, "minimum_should_match": 1},
            context_store_id=context_store_id,
        )
        elements = payload.get("elements", []) if isinstance(payload, Mapping) else []
        products: list[Product] = []
        for raw in elements:
            if not isinstance(raw, Mapping):
                continue
            if eco and not self._is_eco(raw):
                continue
            product = self._product_from_raw(
                raw,
                context_store_id=context_store_id,
            )
            if product.id and product.name:
                products.append(product)
            if len(products) >= requested:
                break
        return products

    @staticmethod
    def _clean_html(value: str) -> str:
        text = re.sub(r"(?i)<br\s*/?>", "\n", value)
        text = re.sub(r"(?s)<[^>]*>", "", text)
        text = re.sub(r"<[^>]*$", "", text)
        return html.unescape(text).replace("\xa0", " ").strip()

    def _aecoc_value(self, details: Any) -> str:
        if not isinstance(details, list):
            return ""
        selected: str = ""
        for detail in details:
            if not isinstance(detail, Mapping):
                continue
            value = str(detail.get("value", ""))
            if str(detail.get("language", "")).lower() == self.language:
                return self._clean_html(value)
            if value and not selected:
                selected = value
        return self._clean_html(selected)

    def product(
        self,
        product_id: str,
        *,
        postal_code: str | None = None,
    ) -> Product:
        context_store_id = self._context_store(postal_code)
        product_id = product_id.strip()
        payload = self._json(
            "GET",
            f"{_CATALOG_BASE}/catalog/products/{quote(product_id, safe='')}/search",
            context_store_id=context_store_id,
        )
        if not isinstance(payload, Mapping) or not payload.get("id"):
            raise ProviderError(f"Gadis product {product_id!r} was not found")
        base = self._product_from_raw(
            payload,
            context_store_id=context_store_id,
        )
        detail: dict[str, str] = {}
        for item in payload.get("aecoc_properties", []):
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("code", ""))
            value = self._aecoc_value(item.get("details"))
            if value:
                detail[code] = value
        return replace(
            base,
            origin=detail.get("ORIGE"),
            ingredients=detail.get("INFIN"),
            nutrients=detail.get("INFNU"),
        )

    @classmethod
    def _convert_categories(
        cls,
        categories: Iterable[Any],
        depth: int,
    ) -> list[dict[str, Any]]:
        if depth <= 0:
            return []
        result: list[dict[str, Any]] = []
        for raw in categories:
            if not isinstance(raw, Mapping):
                continue
            nested = raw.get("nested_categories", {})
            children = (
                nested.get("categories", []) if isinstance(nested, Mapping) else []
            )
            node: dict[str, Any] = {
                "id": str(raw.get("id", "")),
                "name": str(raw.get("name", "")),
            }
            converted = cls._convert_categories(children, depth - 1)
            if converted:
                node["children"] = converted
            result.append(node)
        return result

    def categories(
        self,
        *,
        depth: int = 1,
        postal_code: str | None = None,
    ) -> list[dict[str, Any]]:
        context_store_id = self._context_store(postal_code)
        payload = self._json(
            "GET",
            f"{_CATALOG_BASE}/catalog/categories",
            context_store_id=context_store_id,
        )
        categories = payload.get("categories", []) if isinstance(payload, Mapping) else []
        return self._convert_categories(categories, max(1, depth))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
