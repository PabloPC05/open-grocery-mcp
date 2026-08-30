"""Public Día catalogue client using server-rendered HTML scraping."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from open_grocery_mcp.errors import LocationRequired, ProviderError
from open_grocery_mcp.models import Product

_BASE = "https://www.dia.es"
_POSTAL_RE = re.compile(r"\d{5}")


class _ProductCardParser(HTMLParser):
    """Extract product data from Día's search HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.products: list[dict[str, str]] = []
        self._in_card = False
        self._card_depth = 0
        self._current_attrs: dict[str, str] = {}
        self._price_depth = 0
        self._price_parts: list[str] = []
        self._name_depth = 0
        self._name_parts: list[str] = []
        self._link_href: str | None = None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        
        # Start of product card
        if attrs.get("data-test-id") == "product-card":
            # Save previous card if any
            if self._in_card:
                self._save_current_product()
            
            self._in_card = True
            self._card_depth = 1
            self._current_attrs = {k: v or "" for k, v in attrs.items()}
            self._price_parts = []
            self._name_parts = []
            self._link_href = None
            return
        
        if not self._in_card:
            return
        
        # Track div depth inside card
        if tag == "div":
            self._card_depth += 1
        
        # Extract product link
        if tag == "a" and "href" in attrs:
            href = attrs.get("href", "")
            if "/p/" in href:
                self._link_href = href
        
        # Price container - detect by test-id or by span tags
        test_id = attrs.get("data-test-id") or ""
        if "price" in test_id.lower() or (tag == "span" and not self._name_depth):
            self._price_depth = 1
        elif self._price_depth and tag not in {"br", "img"}:
            self._price_depth += 1
        
        # Name/title container
        test_id = attrs.get("data-test-id") or ""
        if any(x in test_id.lower() for x in ("name", "title")) or tag in ("h3", "h4"):
            self._name_depth = 1
        elif self._name_depth and tag not in {"br", "img"}:
            self._name_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._price_depth:
            self._price_depth -= 1
        
        if self._name_depth:
            self._name_depth -= 1
        
        # Track div depth and save when we close the card div
        if tag == "div" and self._in_card:
            self._card_depth -= 1
            if self._card_depth == 0:
                self._save_current_product()
                self._in_card = False

    def _save_current_product(self) -> None:
        """Save the current product if it has required fields."""
        product_id = self._current_attrs.get("object_id", "").strip()
        if product_id and (self._name_parts or self._price_parts):
            self.products.append({
                "id": product_id,
                "name": " ".join(self._name_parts).strip(),
                "price": "".join(self._price_parts).strip(),
                "brand": self._current_attrs.get("brand", ""),
                "category": self._current_attrs.get("l2_category_description", ""),
                "url": self._link_href or "",
            })
        self._current_attrs = {}

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text or not self._in_card:
            return
        
        if self._price_depth:
            self._price_parts.append(text)
        
        if self._name_depth:
            self._name_parts.append(text)


def _parse_euro_price(value: str) -> Decimal | None:
    """Parse Día price format: '5,04 €' or '5,04 €\n\n(0,84 €/LITRO)'"""
    text = str(value or "").strip()
    if not text:
        return None
    # Extract first price (before unit price in parentheses)
    match = re.search(r"([\d,]+)\s*€", text)
    if not match:
        return None
    try:
        numeric = match.group(1).replace(",", ".")
        return Decimal(numeric)
    except (InvalidOperation, ValueError):
        return None


def _parse_unit_price(value: str) -> tuple[Decimal, str] | None:
    """Extract unit price from text like '(0,84 €/LITRO)' or '(0,96 €/L)'"""
    text = str(value or "").strip()
    match = re.search(r"\(([\d,]+)\s*€\s*/\s*([A-Z]+)\)", text, re.I)
    if not match:
        return None
    try:
        price = Decimal(match.group(1).replace(",", "."))
        unit_raw = match.group(2).upper()
        # Normalize unit
        if unit_raw in ("LITRO", "L"):
            unit = "L"
        elif unit_raw in ("KG", "KILO", "KILOGRAMO"):
            unit = "kg"
        else:
            unit = unit_raw.lower()
        return price, unit
    except (InvalidOperation, ValueError):
        return None


def parse_products(html: str) -> list[Product]:
    """Parse Día search HTML into Product objects."""
    parser = _ProductCardParser()
    parser.feed(html)
    
    products: list[Product] = []
    seen: set[str] = set()
    
    for raw in parser.products:
        product_id = raw["id"]
        if product_id in seen:
            continue
        seen.add(product_id)
        
        name = raw["name"]
        if not name:
            continue
        
        price = _parse_euro_price(raw["price"])
        if price is None or price <= 0:
            continue
        
        # Parse unit price
        unit_price_data = _parse_unit_price(raw["price"])
        price_per_unit = unit_price_data[0] if unit_price_data else None
        unit = unit_price_data[1] if unit_price_data else None
        
        # Build product URL
        url_path = raw["url"]
        url = urljoin(_BASE + "/", url_path) if url_path else None
        
        metadata: dict[str, object] = {}
        if raw.get("brand"):
            metadata["brand_name"] = raw["brand"]
        if raw.get("category"):
            metadata["category_name"] = raw["category"]
        
        products.append(
            Product(
                store="dia",
                id=product_id,
                name=name,
                price=price,
                currency="EUR",
                price_per_unit=price_per_unit,
                unit=unit,
                brand=raw.get("brand"),
                category=raw.get("category"),
                available=True,
                url=url,
                metadata=metadata,
            )
        )
    
    return products


class DiaCatalogueProvider:
    """Día catalogue provider using HTML scraping."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
        state_path: str | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9",
            },
        )
        self._state_path = state_path

    @staticmethod
    def _postal_code(value: str | None) -> str:
        """Validate Spanish 5-digit postal code."""
        postal_code = str(value or "").strip()
        if not _POSTAL_RE.fullmatch(postal_code):
            raise LocationRequired("Día requires a five-digit Spanish postal code")
        return postal_code

    def _get_search_html(self, term: str) -> str:
        """Fetch search results HTML from Día."""
        # Día's search is public but may require cookies from a browser session
        # to avoid anti-bot blocking (similar to Eroski)
        try:
            response = self._client.get(
                f"{_BASE}/search",
                params={"q": term},
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not search Día: {exc}") from exc
        
        # Check for anti-bot blocking
        if response.status_code in (403, 429):
            raise ProviderError(
                "Día catalogue is blocked by anti-bot protection; "
                "use local MCP with login_dia or import_browser_session"
            )
        
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Día catalogue returned HTTP {exc.response.status_code}"
            ) from exc
        
        # Verify we're on dia.es
        parsed = urlsplit(str(response.url))
        if parsed.hostname != "www.dia.es":
            raise ProviderError("Día catalogue redirected to an untrusted host")
        
        return response.text

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
    ) -> list[Product]:
        """Search Día catalogue by keyword.
        
        Args:
            query: Search term in Spanish
            limit: Maximum number of products to return (1-100)
            postal_code: Spanish 5-digit postal code (validated but not used for pricing)
        
        Returns:
            List of Product objects
        
        Raises:
            LocationRequired: If postal_code is provided but invalid
            ProviderError: If search fails or is blocked
        """
        term = query.strip()
        if not term:
            return []
        
        # Validate postal code if provided (for consistency with other providers)
        if postal_code is not None:
            self._postal_code(postal_code)
        
        html = self._get_search_html(term)
        products = parse_products(html)
        
        return products[: max(1, min(limit, 100))]

    def catalogue_contract(self) -> dict[str, Any]:
        """Return catalogue contract metadata."""
        return {
            "pagination": "server_rendered_html",
            "maximum_page_size": 100,
            "exact_total": False,
            "category_search": False,
            "geography": "postal_code_validated_public_catalogue",
            "cache_safe": True,
            "hard_limit": "first HTML page; no verified pagination contract",
        }

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        """Get a single product by ID (via search)."""
        target = str(product_id).strip()
        if not target:
            raise ProviderError("Día product id cannot be empty")
        
        # Search for the product ID
        for product in self.search(target, limit=100, postal_code=postal_code):
            if product.id == target:
                return product
        
        raise ProviderError(f"Día product {target!r} was not found")

    def close(self) -> None:
        """Close the HTTP client if owned."""
        if self._owns_client:
            self._client.close()


__all__ = ["DiaCatalogueProvider", "parse_products"]
