"""Carrefour Spain catalogue client using Empathy search platform."""

from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from open_grocery_mcp.errors import LocationRequired, ProviderError
from open_grocery_mcp.models import Product
from open_grocery_mcp.state_dir import get_state_dir

_BASE = "https://www.carrefour.es"
_POSTAL_RE = re.compile(r"\d{5}")
_CAPTCHA_RE = re.compile(
    r"<title\b[^>]*>[^<]*(?:captcha|access denied|robot|verificaci[oó]n|attention required)[^<]*</title>",
    re.I,
)
_CLOUDFLARE_CHALLENGE_RE = re.compile(
    r"(?:cloudflare|cf-|__cf_bm|checking your browser|challenge-platform)",
    re.I,
)


class CarrefourCatalogueProvider:
    """Public Carrefour Spain catalogue via Empathy search API.
    
    Cloudflare protects the search endpoint. Direct HTTP 403s without
    browser cookies. Local MCP can retry with storage_state.json cookies
    from a browser session. Hosted Vercel/Lambda will fail with anti-bot
    error (same class of limitation as Eroski local session requirement).
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Accept-Language": "es-ES,es;q=0.9",
                "Referer": f"{_BASE}/supermercado",
            },
        )
        self._storage_state_path = get_state_dir() / "carrefour" / "storage_state.json"

    @staticmethod
    def _postal_code(value: str | None) -> str:
        """Validate Spanish postal code format."""
        postal_code = str(value or "").strip()
        if not _POSTAL_RE.fullmatch(postal_code):
            raise LocationRequired("Carrefour requires a five-digit Spanish postal code")
        return postal_code

    @staticmethod
    def _is_challenge_html(text: str) -> bool:
        """Detect Cloudflare/reCAPTCHA challenge or anti-bot HTML."""
        return bool(
            _CAPTCHA_RE.search(text) or _CLOUDFLARE_CHALLENGE_RE.search(text)
        )

    def _load_browser_cookies(self) -> None:
        """Load non-expired cookies from Playwright storage_state.json into client."""
        if not self._storage_state_path.exists():
            return
        
        try:
            with open(self._storage_state_path, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            return
        
        if not isinstance(state, dict):
            return
        
        cookies_data = state.get("cookies", [])
        if not isinstance(cookies_data, list):
            return
        
        now = time.time()
        
        for cookie in cookies_data:
            if not isinstance(cookie, dict):
                continue
            
            domain = str(cookie.get("domain", "")).lstrip(".").casefold()
            if not domain or "carrefour.es" not in domain:
                continue
            
            # Skip expired cookies
            expires = cookie.get("expires", -1)
            if expires != -1 and expires < now:
                continue
            
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or not value:
                continue
            
            # Set cookie in httpx client
            self._client.cookies.set(
                name=name,
                value=value,
                domain=domain if domain.startswith(".") else f".{domain}",
            )

    def _search_empathy(
        self,
        query: str,
        *,
        limit: int = 10,
        start: int = 0,
        load_session_cookies: bool = False,
    ) -> dict[str, Any]:
        """Call Empathy search API with Carrefour-specific parameters."""
        url = f"{_BASE}/search-api/query/v1/search"
        
        params = {
            "query": query,
            "rows": max(1, min(limit, 100)),
            "start": start,
            "instance": "x-carrefour",
            "lang": "es",
            "catalog": "food",
            "scope": "desktop",
            "warehouse": os.getenv("CARREFOUR_WAREHOUSE", ""),
        }
        
        # Remove empty params
        params = {k: v for k, v in params.items() if v or v == 0}
        
        if load_session_cookies:
            self._load_browser_cookies()
        
        try:
            response = self._client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (403, 429, 503):
                # Cloudflare anti-bot
                raise ProviderError(
                    f"Carrefour search blocked by Cloudflare (HTTP {status}). "
                    "For local MCP, use import_browser_session(store='carrefour') "
                    "or login_with_browser(store='carrefour') to save a browser session. "
                    "Hosted MCP cannot access Carrefour catalogue."
                ) from exc
            raise ProviderError(
                f"Carrefour search returned HTTP {status}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not search Carrefour: {exc}") from exc
        
        # Check for challenge HTML
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            if self._is_challenge_html(response.text):
                raise ProviderError(
                    "Carrefour returned anti-bot challenge HTML. "
                    "For local MCP, use import_browser_session(store='carrefour') "
                    "or login_with_browser(store='carrefour') to save a browser session. "
                    "Hosted MCP cannot access Carrefour catalogue."
                )
        
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("Carrefour returned invalid JSON") from exc

    @staticmethod
    def _parse_price(value: Any) -> Decimal | None:
        """Parse price value to Decimal."""
        if value is None or value == "":
            return None
        try:
            if isinstance(value, (int, float)):
                return Decimal(str(value))
            text = str(value).strip()
            # Remove currency symbols and spaces
            numeric = re.sub(r"[^0-9,.-]", "", text)
            # Convert comma decimal to dot
            normalized = numeric.replace(",", ".")
            return Decimal(normalized)
        except (InvalidOperation, ValueError):
            return None

    def _product_from_empathy_result(self, raw: dict[str, Any]) -> Product | None:
        """Map Empathy API result to Product model.
        
        Empathy uses __ prefix for standard fields per their API contract.
        """
        # Empathy standard fields with __ prefix
        product_id = str(raw.get("__id", raw.get("id", ""))).strip()
        name = str(raw.get("__name", raw.get("name", ""))).strip()
        
        # Price can be in multiple formats
        price_data = raw.get("__price", raw.get("price"))
        if isinstance(price_data, dict):
            price = self._parse_price(
                price_data.get("value", price_data.get("current", {}).get("value"))
            )
            # Unit price might be in price.unit or referencePrice
            unit_price_data = price_data.get("unit") or price_data.get("referencePrice")
            if isinstance(unit_price_data, dict):
                unit_price = self._parse_price(unit_price_data.get("value"))
            else:
                unit_price = self._parse_price(unit_price_data)
        else:
            price = self._parse_price(price_data)
            unit_price = self._parse_price(raw.get("__unitPrice", raw.get("unitPrice")))
        
        if not product_id or not name or not price or price <= 0:
            return None
        
        # URL
        url_path = str(raw.get("__url", raw.get("url", ""))).strip()
        url = urljoin(_BASE + "/", url_path) if url_path else None
        if url:
            url_parts = urlsplit(url)
            if url_parts.scheme != "https" or "carrefour.es" not in (url_parts.hostname or ""):
                url = None
        
        # Images
        images = raw.get("__images", raw.get("images", []))
        image_url = None
        if isinstance(images, list) and images:
            image_url = str(images[0]).strip() if images[0] else None
        elif isinstance(images, str):
            image_url = images.strip() or None
        
        # Brand
        brand = str(raw.get("__brand", raw.get("brand", ""))).strip() or None
        
        # EAN
        ean = str(raw.get("__ean", raw.get("ean", ""))).strip() or None
        
        # Availability
        available = bool(raw.get("__available", raw.get("available", True)))
        
        metadata: dict[str, Any] = {}
        if image_url:
            metadata["image_url"] = image_url
        
        # Add promotion info if present
        promotion_raw = raw.get("__promotion", raw.get("promotion"))
        if promotion_raw:
            metadata["promotion"] = promotion_raw
        
        return Product(
            store="carrefour",
            id=product_id,
            name=name,
            price=price,
            currency="EUR",
            price_per_unit=unit_price if unit_price and unit_price > 0 else None,
            brand=brand,
            available=available,
            url=url,
            ean=ean,
            metadata=metadata,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        """Search products in Carrefour catalogue.
        
        Args:
            query: Search term
            limit: Maximum results (1-100)
            postal_code: Spanish 5-digit postal code (validated but not required by API)
            eco: Filter ecological products (applied post-search)
        
        Returns:
            List of Product objects
        
        Raises:
            ProviderError: On anti-bot block or network error
            LocationRequired: If postal_code format invalid
        """
        term = query.strip()
        if not term:
            return []
        
        # Validate postal code if provided
        if postal_code is not None:
            self._postal_code(postal_code)
        
        # Try without cookies first
        is_vercel = any(
            os.getenv(key) for key in ("VERCEL", "VERCEL_ENV", "AWS_LAMBDA_FUNCTION_NAME")
        )
        
        try:
            data = self._search_empathy(term, limit=limit)
        except ProviderError as exc:
            # Retry with browser cookies if local and not Vercel
            msg = str(exc).lower()
            if not is_vercel and ("403" in msg or "429" in msg or "503" in msg or "challenge" in msg):
                try:
                    data = self._search_empathy(term, limit=limit, load_session_cookies=True)
                except ProviderError:
                    # If retry also fails, raise original error
                    raise exc from None
            else:
                raise
        
        # Parse Empathy response - try multiple possible structures
        results = None
        
        # Try direct results array
        if "results" in data and isinstance(data["results"], list):
            results = data["results"]
        # Try catalog.content
        elif "catalog" in data:
            catalog = data["catalog"]
            if isinstance(catalog, dict):
                if isinstance(catalog.get("content"), list):
                    results = catalog["content"]
                elif isinstance(catalog.get("content"), dict):
                    docs = catalog["content"].get("docs")
                    if isinstance(docs, list):
                        results = docs
        # Try content.docs
        elif "content" in data:
            content = data["content"]
            if isinstance(content, dict):
                docs = content.get("docs")
                if isinstance(docs, list):
                    results = docs
        
        if results is None or not isinstance(results, list):
            return []
        
        products: list[Product] = []
        for raw in results:
            if not isinstance(raw, dict):
                continue
            
            product = self._product_from_empathy_result(raw)
            if product:
                products.append(product)
        
        # Apply eco filter if requested
        if eco:
            eco_pattern = re.compile(
                r"\b(?:eco|ecol[oó]gico|bio|org[aá]nico)\b", re.I
            )
            products = [p for p in products if eco_pattern.search(p.name)]
        
        return products[:limit]

    def catalogue_contract(self) -> dict[str, Any]:
        """Return catalogue capabilities and limitations."""
        return {
            "pagination": "empathy_search",
            "maximum_page_size": 100,
            "exact_total": True,
            "category_search": False,
            "geography": "postal_code_validated_warehouse_aware",
            "cache_safe": False,
            "hard_limit": "Cloudflare anti-bot protection; hosted deployments blocked",
            "local_session_required": True,
        }

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        """Get single product by ID.
        
        Empathy search doesn't have a direct product endpoint,
        so we search by ID and match exact product.
        """
        target = str(product_id).strip()
        if not target:
            raise ProviderError("Carrefour product id cannot be empty")
        
        # Search by product ID
        for product in self.search(target, limit=50, postal_code=postal_code):
            if product.id == target:
                return product
        
        raise ProviderError(f"Carrefour product {target!r} was not found")

    def close(self) -> None:
        """Close HTTP client if owned."""
        if self._owns_client:
            self._client.close()


__all__ = ["CarrefourCatalogueProvider"]
