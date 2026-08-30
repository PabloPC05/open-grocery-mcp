"""Public Eroski catalogue client for the server-rendered search results."""

from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from open_grocery_mcp.errors import LocationRequired, ProviderError
from open_grocery_mcp.models import Product

_BASE = "https://supermercado.eroski.es"
_POSTAL_RE = re.compile(r"\d{5}")
_PRODUCT_ID_RE = re.compile(r"/productdetail/(\d+)(?:-|/)", re.I)
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_PASSWORD_INPUT_RE = re.compile(
    r"<input\b(?=[^>]*\btype\s*=\s*[\"']password[\"'])[^>]*>",
    re.I,
)
_LOGIN_PAGE_RE = re.compile(
    r"<form\b[^>]*\baction\s*=\s*[\"'][^\"']*/es/login(?:/|[\"'])",
    re.I,
)
_CHALLENGE_TITLE_RE = re.compile(
    r"<title\b[^>]*>[^<]*(?:captcha|access denied|robot|verificaci[oó]n|comprobando tu navegador)[^<]*</title>",
    re.I,
)
_RECAPTCHA_INTERSTITIAL_RE = re.compile(
    r"<base\b[^>]*\bhref\s*=\s*[\"'][^\"']*recaptcha[^\"']*challenge[^\"']*[\"']",
    re.I,
)
_ECO_RE = re.compile(r"\b(?:eco|ecologico|ecológica|ecológico|bio|organico|orgánica|orgánico)\b", re.I)
_PROMO_QUANTITY_RE = re.compile(
    r"\b(?P<buy>\d+)\s*(?:x|×)\s*(?P<pay>\d+)\b", re.I
)
_PROMO_UNIT_RE = re.compile(
    r"\b(?P<quantity>\d+)\s*(?:ª\s*)?(?:unidad(?:es)?|ud(?:s?)?)(?:\b|$)",
    re.I,
)
_PROMO_SECOND_UNIT_RE = re.compile(
    r"\b(?P<quantity>\d+)\s*"
    r"(?:\u00aa|\u00ba|\u00c2\u00aa|\ufffd|a)?\.?\s*"
    r"(?:unidad(?:es)?|ud(?:s?)?)\s*[-:]?\s*"
    r"(?P<discount>\d+(?:[.,]\d+)?)\s*%",
    re.I,
)
_PREVIOUS_PRICE_CLASSES = {
    "price-offer-before",
    "price-before",
    "price-old",
    "price-original",
    "price-regular",
    "price-was",
}


class _ProductSearchParser(HTMLParser):
    """Extract normalized product cards without depending on browser selectors."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.products: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._card_tag: str | None = None
        self._div_depth = 0
        self._name_depth = 0
        self._price_depth = 0
        self._name_parts: list[str] = []
        self._price_parts: list[str] = []
        self._previous_price_depth = 0
        self._previous_price_parts: list[str] = []
        self._promotion_depth = 0
        self._promotion_parts: list[str] = []

    @staticmethod
    def _classes(attrs: dict[str, str | None]) -> set[str]:
        return {
            value.casefold()
            for value in str(attrs.get("class") or "").split()
            if value
        }

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        classes = self._classes(attrs)
        if self._current is None:
            if "product-item-lineal" in classes:
                self._current = {}
                self._card_tag = tag
                self._div_depth = 1
            return

        if tag == self._card_tag or (
            self._card_tag == "div" and tag == "div"
        ):
            self._div_depth += 1
        if tag == "a" and "product-title-link" in classes and "url" not in self._current:
            self._current["url"] = str(attrs.get("href") or "")
            self._name_depth = 1
            self._name_parts = []
        elif self._name_depth and tag not in _VOID_TAGS:
            self._name_depth += 1
        if tag == "span" and "price-offer-now" in classes:
            self._price_depth = 1
            self._price_parts = []
        elif self._price_depth and tag not in _VOID_TAGS:
            self._price_depth += 1
        if classes & _PREVIOUS_PRICE_CLASSES:
            self._previous_price_depth = 1
            self._previous_price_parts = []
        elif self._previous_price_depth and tag not in _VOID_TAGS:
            self._previous_price_depth += 1
        is_promotion = (
            not any(name.startswith("price-") for name in classes)
            and any(
                "promo" in name
                or "discount" in name
                or "coupon" in name
                or "offer" in name
                or name in {"badge", "campaign", "saving", "promotion"}
                for name in classes
            )
        )
        if is_promotion:
            self._promotion_depth = 1
            self._promotion_parts = []
            for attribute in (
                "data-promotion-type",
                "data-offer-type",
                "data-promo-type",
            ):
                value = str(attrs.get(attribute) or "").strip()
                if value:
                    self._current["promotion_type"] = value
                    break
        elif self._promotion_depth and tag not in _VOID_TAGS:
            self._promotion_depth += 1
        for attribute in ("data-promotion", "data-offer", "data-promo"):
            value = str(attrs.get(attribute) or "").strip()
            if value:
                self._current["promotion_label"] = value
                break
        if tag == "img" and "product-img" in classes:
            self._current.setdefault("image_url", str(attrs.get("src") or ""))
            self._current.setdefault("name", str(attrs.get("alt") or "").strip())

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if self._name_depth:
            self._name_depth -= 1
            if self._name_depth == 0 and self._name_parts:
                self._current.setdefault("name", " ".join(self._name_parts).strip())
        if self._price_depth:
            self._price_depth -= 1
            if self._price_depth == 0 and self._price_parts:
                self._current.setdefault("price", "".join(self._price_parts).strip())
        if self._previous_price_depth:
            self._previous_price_depth -= 1
            if self._previous_price_depth == 0 and self._previous_price_parts:
                self._current.setdefault(
                    "previous_price", "".join(self._previous_price_parts).strip()
                )
        if self._promotion_depth:
            self._promotion_depth -= 1
            if self._promotion_depth == 0 and self._promotion_parts:
                label = " ".join(self._promotion_parts).strip()
                if label:
                    existing = self._current.get("promotion_label", "")
                    self._current["promotion_label"] = (
                        f"{existing}; {label}" if existing else label
                    )
        if tag == self._card_tag or (
            self._card_tag == "div" and tag == "div"
        ):
            self._div_depth -= 1
            if self._div_depth == 0:
                self.products.append(self._current)
                self._current = None
                self._card_tag = None

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if not value:
            return
        if self._name_depth:
            self._name_parts.append(value)
        if self._price_depth:
            self._price_parts.append(value)
        if self._previous_price_depth:
            self._previous_price_parts.append(value)
        if self._promotion_depth:
            self._promotion_parts.append(value)


def _parse_euro_price(value: str) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        numeric = re.sub(r"[^0-9,.-]", "", text)
        return Decimal(numeric.replace(".", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _quantity_mechanic(label: str) -> dict[str, int | float] | None:
    multiple = _PROMO_QUANTITY_RE.search(label)
    if multiple:
        return {
            "buy_quantity": int(multiple.group("buy")),
            "pay_quantity": int(multiple.group("pay")),
        }
    unit = _PROMO_UNIT_RE.search(label)
    second_unit = _PROMO_SECOND_UNIT_RE.search(label)
    if second_unit:
        return {
            "buy_quantity": int(second_unit.group("quantity")),
            "discount_percent": float(
                second_unit.group("discount").replace(",", ".")
            ),
        }
    if unit:
        return {"buy_quantity": int(unit.group("quantity"))}
    return None


def _promotion_type(raw_type: str, label: str) -> str | None:
    explicit = " ".join(str(raw_type or "").split())
    if explicit:
        return explicit
    lowered = label.casefold()
    if _PROMO_QUANTITY_RE.search(label):
        return "multibuy"
    if "cupón" in lowered or "cupon" in lowered or "coupon" in lowered:
        return "coupon"
    if "descuento" in lowered or "discount" in lowered:
        return "discount"
    if "fidelidad" in lowered or "tarjeta" in lowered or "card" in lowered:
        return "loyalty"
    return "offer" if label else None


def _promotion_metadata(raw: dict[str, str], current_price: Decimal) -> dict[str, object] | None:
    label = " ".join(str(raw.get("promotion_label") or "").split())
    previous_price = _parse_euro_price(raw.get("previous_price", ""))
    promotion_type = _promotion_type(raw.get("promotion_type", ""), label)
    quantity_mechanic = _quantity_mechanic(label)
    if not label and previous_price is None and promotion_type is None:
        return None
    promotion: dict[str, object] = {"current_price": float(current_price)}
    if previous_price is not None and previous_price > 0:
        promotion["previous_price"] = float(previous_price)
    if label:
        promotion["label"] = label
    if promotion_type:
        promotion["type"] = promotion_type
    if quantity_mechanic:
        promotion["quantity_mechanic"] = quantity_mechanic
    return promotion


def parse_products(html: str) -> list[Product]:
    parser = _ProductSearchParser()
    parser.feed(html)
    products: list[Product] = []
    seen: set[str] = set()
    for raw in parser.products:
        url = urljoin(_BASE + "/", raw.get("url", ""))
        url_parts = urlsplit(url)
        if url_parts.scheme != "https" or url_parts.hostname != "supermercado.eroski.es":
            continue
        match = _PRODUCT_ID_RE.search(url)
        name = raw.get("name", "").strip()
        price = _parse_euro_price(raw.get("price", ""))
        if price is None:
            continue
        if not match or not name or price <= 0 or match.group(1) in seen:
            continue
        product_id = match.group(1)
        seen.add(product_id)
        image_path = raw.get("image_url", "").strip()
        image_url = urljoin(_BASE + "/", image_path) if image_path else None
        if image_url:
            image_parts = urlsplit(image_url)
            if (
                image_parts.scheme != "https"
                or image_parts.hostname != "supermercado.eroski.es"
            ):
                image_url = None
        metadata: dict[str, object] = {
            "image_url": image_url,
            # The public search response does not expose a delivery
            # area/store identifier. A postal code is validated by the
            # provider API, but it must not be presented as proof that
            # this price or assortment is location-specific.
            "location_aware": False,
        }
        promotion = _promotion_metadata(raw, price)
        if promotion is not None:
            metadata["promotion"] = promotion
        products.append(
            Product(
                store="eroski",
                id=product_id,
                name=name,
                price=price,
                currency="EUR",
                available=True,
                url=url,
                metadata=metadata,
            )
        )
    return products


class EroskiCatalogueProvider:
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
        self._session_loaded = False

    @staticmethod
    def _postal_code(value: str | None) -> str:
        postal_code = str(value or "").strip()
        if not _POSTAL_RE.fullmatch(postal_code):
            raise LocationRequired("Eroski requires a five-digit Spanish postal code")
        return postal_code

    def _try_load_local_session(self) -> bool:
        """Try to load cookies from a local Playwright storage_state.json.
        
        Returns True if session cookies were loaded, False otherwise.
        This only works in local MCP environments where a browser session exists.
        """
        if self._session_loaded:
            return False
        
        # Check if we're in a hosted/serverless environment
        if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
            return False
        
        # Determine state path
        if self._state_path:
            state_path = Path(self._state_path).expanduser()
        else:
            state_dir = Path(
                os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")
            ).expanduser()
            state_path = state_dir / "eroski" / "storage_state.json"
        
        if not state_path.is_file():
            return False
        
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        
        if not isinstance(state, Mapping):
            return False
        
        # Load official-domain cookies
        now = time.time()
        loaded = False
        for row in state.get("cookies", []) or []:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name", ""))
            value = str(row.get("value", ""))
            domain = str(row.get("domain", "")).strip()
            normalized_domain = domain.lstrip(".").casefold()
            
            if normalized_domain not in {"eroski.es", "supermercado.eroski.es"}:
                continue
            
            path = str(row.get("path", "/")) or "/"
            try:
                expires = float(row.get("expires", -1))
            except (TypeError, ValueError):
                continue
            
            if expires > 0 and expires <= now:
                continue
            
            if name and value:
                self._client.cookies.set(
                    name,
                    value,
                    domain=domain or "supermercado.eroski.es",
                    path=path,
                )
                loaded = True
        
        if loaded:
            self._session_loaded = True
        
        return loaded

    @staticmethod
    def _is_trusted_response(response: httpx.Response) -> bool:
        target = urlsplit(str(response.url))
        return target.scheme == "https" and target.hostname == "supermercado.eroski.es"

    @staticmethod
    def _is_auth_challenge(response: httpx.Response) -> bool:
        path = urlsplit(str(response.url)).path.rstrip("/").casefold()
        if path in {"/es/login", "/es/login/only"}:
            return True
        text = response.text
        return bool(
            _PASSWORD_INPUT_RE.search(text)
            or _LOGIN_PAGE_RE.search(text) and not _PRODUCT_ID_RE.search(text)
            or _CHALLENGE_TITLE_RE.search(text)
            or _RECAPTCHA_INTERSTITIAL_RE.search(text)
        )

    def _get_search_html(self, term: str, *, _retry_with_session: bool = True) -> str:
        try:
            response = self._client.get(
                _BASE + "/es/search/results/",
                params={"q": term},
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not search Eroski: {exc}") from exc
        
        # Treat HTTP 403/429 as anti-bot challenge before raise_for_status
        if response.status_code in (403, 429):
            if _retry_with_session and self._try_load_local_session():
                return self._get_search_html(term, _retry_with_session=False)
            raise ProviderError(
                "Eroski catalogue is blocked by anti-bot protection (datacenter IPs often trigger reCAPTCHA); "
                "use local MCP with a saved browser session"
            )
        
        # Check for recaptcha interstitial even on HTTP 200
        if response.status_code == 200 and self._is_auth_challenge(response):
            if _retry_with_session and self._try_load_local_session():
                return self._get_search_html(term, _retry_with_session=False)
            raise ProviderError(
                "Eroski catalogue returned anti-bot challenge (reCAPTCHA interstitial); "
                "use local MCP with a saved browser session"
            )
        
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Eroski catalogue returned HTTP {exc.response.status_code}"
            ) from exc
        
        if not self._is_trusted_response(response):
            raise ProviderError("Eroski catalogue redirected to an untrusted host")
        
        return response.text

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        term = query.strip()
        if not term:
            return []
        # Eroski's public search page is readable without a session. The old
        # homepage bootstrap was not a location-selection API: its normal
        # navigation contains login links and caused false challenge errors.
        if postal_code is not None:
            self._postal_code(postal_code)
        products = parse_products(self._get_search_html(term))
        if eco:
            products = [product for product in products if _ECO_RE.search(product.name)]
        return products[: max(1, min(limit, 100))]

    def catalogue_contract(self) -> dict[str, Any]:
        return {
            "pagination": "server_rendered_first_page",
            "maximum_page_size": 100,
            "exact_total": False,
            "category_search": False,
            "geography": "postal_code_validated_public_catalogue",
            "cache_safe": True,
            "hard_limit": "first rendered search page; no verified next-page contract",
        }

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        target = str(product_id).strip()
        if not target:
            raise ProviderError("Eroski product id cannot be empty")
        for product in self.search(target, limit=100, postal_code=postal_code):
            if product.id == target:
                return product
        raise ProviderError(f"Eroski product {target!r} was not found")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["EroskiCatalogueProvider", "parse_products"]
