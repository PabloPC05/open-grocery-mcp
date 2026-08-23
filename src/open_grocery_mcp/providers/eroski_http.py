"""Authenticated Eroski HTTP client built from the verified Tapestry contract.

Eroski's storefront is a server-rendered Apache Tapestry 5 application: there
is no JSON API, so this client drives the same forms the browser uses.

Verified live contract (value-free):

- session cookies come from the saved Playwright ``storage_state``;
  a first ``GET /?zipCode=<cp>`` establishes the delivery context;
- ``GET /es/search/results/?q=<term>`` renders one
  ``form[action*="productlistadditem"]`` per result carrying the signed
  ``t:formdata`` CSRF token; submitting it URL-encoded (the ``a.toAddProduct``
  trigger) adds that tile's product;
- ``GET /es/mycart/?basketType=ALI`` renders rows
  ``div.row.shopping-cart-item`` containing ``[class*=basket-product-{pid}]``,
  a quantity input and an ``a.remove-item-shopping-btn-cart`` removal link
  backed by ``POST .../basketproduct.basketadditemcomponent:addtocart`` with
  ``product=<id>`` plus zone fields parsed from the same page;
- the header total (``.shopping-cart__totalprice .price``) is the basket total.

Order submission is not implemented here by design: Eroski places real orders
through its order endpoint with no separate checkout step.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin

import httpx

from open_grocery_mcp.errors import AuthenticationRequired, ProviderError

_BASE = "https://supermercado.eroski.es"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_FORM_RE = re.compile(
    r'<form[^>]*action="(?P<action>[^"]*productlistadditem[^"]*)"[^>]*>(?P<body>.*?)</form>',
    re.S,
)
_FORMDATA_RE = re.compile(r'name="t:formdata"[^>]*value="([^"]*)"')
_QTY_RE = re.compile(
    r'class="[^"]*quantity[^"]*"[^>]*value="([0-9]+)"', re.S
)
_ROW_RE = re.compile(
    r'<div class="row shopping-cart-item".*?</div>\s*</div>\s*</div>', re.S
)
_PRODUCT_ID_RE = re.compile(r"basket-product-(\d+)")
_TOTAL_RE = re.compile(
    r'class="shopping-cart__totalprice[^"]*".*?class="price"[^>]*>\s*([0-9,.]+)', re.S
)
_REMOVE_LINK_RE = re.compile(
    r'class="remove-item-shopping-btn[^"]*"', re.S
)


def _as_decimal(value: Any) -> Any:
    from decimal import Decimal, InvalidOperation

    if value is None or isinstance(value, bool):
        return Decimal("0")
    try:
        result = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


@dataclass
class EroskiCartItem:
    product_id: str
    quantity: int


@dataclass
class EroskiCart:
    items: list[EroskiCartItem] = field(default_factory=list)
    total_text: str = "0,00€"

    @property
    def version(self) -> int:
        """Stable content fingerprint (no server-side counter exists)."""
        material = json.dumps(
            {
                "items": sorted(
                    [i.product_id, str(i.quantity)] for i in self.items
                ),
                "total": self.total_text,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(material).digest()
        return int.from_bytes(digest[:8], "big") >> 1


class EroskiHTTPClient:
    """Read-write authenticated Eroski cart client (Tapestry form driver)."""

    def __init__(
        self,
        *,
        state_path: str | os.PathLike[str] | None = None,
        zip_code: str = "48001",
        timeout: float = 40.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else (
            Path(
                os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")
            ).expanduser()
            / "eroski"
            / "storage_state.json"
        )
        self.zip_code = zip_code
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*",
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        self._context_ready = False

    # ------------------------------------------------------------------ auth

    def _load_session_cookies(self) -> dict[str, str]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            raise AuthenticationRequired(
                "no saved Eroski session; run login_with_browser"
            ) from None
        jar: dict[str, str] = {}
        for row in state.get("cookies", []) or []:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name", ""))
            value = str(row.get("value", ""))
            if name and value:
                jar.setdefault(name, value)
        if "JSESSIONID" not in jar:
            raise AuthenticationRequired(
                "saved Eroski session lacks JSESSIONID; run login_with_browser"
            )
        return jar

    def _ensure_context(self) -> None:
        if self._context_ready:
            return
        for name, value in self._load_session_cookies().items():
            self._client.cookies.set(name, value, domain="supermercado.eroski.es")
        response = self._client.get(f"{_BASE}/", params={"zipCode": self.zip_code})
        if response.status_code != 200:
            raise ProviderError(
                f"Eroski context bootstrap returned HTTP {response.status_code}"
            )
        body = response.text
        if 'type="password"' in body and "Identif" in body:
            raise AuthenticationRequired(
                "Eroski session is not authenticated; run login_with_browser"
            )
        self._context_ready = True

    def _get_html(self, path: str, **params: str) -> str:
        self._ensure_context()
        try:
            response = self._client.get(f"{_BASE}{path}", params=params or None)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Eroski GET failed: {exc}") from exc
        if response.status_code == 401:
            raise AuthenticationRequired("Eroski session rejected (401)")
        if response.status_code >= 400:
            raise ProviderError(
                f"Eroski GET {path} returned HTTP {response.status_code}"
            )
        return response.text

    def _post_form(
        self,
        action_url: str,
        data: dict[str, str],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        url = action_url if action_url.startswith("http") else f"{_BASE}{action_url}"
        headers = dict(extra_headers or {})
        try:
            response = self._client.post(url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Eroski POST failed: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"Eroski POST returned HTTP {response.status_code}")
        return response.text

    # ---------------------------------------------------------------- parsing

    @staticmethod
    def parse_cart(html: str) -> EroskiCart:
        total_match = _TOTAL_RE.search(html)
        total_text = total_match.group(1) + "€" if total_match else "0,00€"
        items: dict[str, EroskiCartItem] = {}
        marker = 'class="row shopping-cart-item"'
        segments = html.split(marker)[1:]
        for segment in segments:
            window = segment[:4000]
            pid_match = _PRODUCT_ID_RE.search(window)
            if not pid_match:
                continue
            pid = pid_match.group(1)
            qty_match = _QTY_RE.search(window)
            qty = int(qty_match.group(1)) if qty_match else 1
            if qty <= 0:
                continue
            items[pid] = EroskiCartItem(product_id=pid, quantity=qty)
        return EroskiCart(items=list(items.values()), total_text=total_text)

    @staticmethod
    def parse_add_forms(html: str) -> list[dict[str, str]]:
        """Return one entry per result tile: action URL + t:formdata."""
        forms = []
        for match in _FORM_RE.finditer(html):
            action = match.group("action")
            body = match.group("body")
            token = _FORMDATA_RE.search(body)
            forms.append(
                {
                    "action": action,
                    "t_formdata": token.group(1) if token else "",
                }
            )
        return forms

    # ------------------------------------------------------------ operations

    def search_add_forms(self, query: str) -> list[dict[str, str]]:
        html = self._get_html("/es/search/results/", q=query)
        return self.parse_add_forms(html)

    def add_to_cart(self, query: str, tile_index: int = 0) -> EroskiCart:
        """Submit the Nth result tile's add form for the given search term."""
        forms = self.search_add_forms(query)
        if tile_index >= len(forms):
            raise ProviderError(
                f"Eroski search {query!r} rendered only {len(forms)} tiles"
            )
        chosen = forms[tile_index]
        action = urljoin(_BASE + "/", chosen["action"])
        # Tapestry event URLs carry the page's query string (verified live:
        # the storefront posts to ...:addtocart?q=<term>).
        separator = "&" if "?" in action else "?"
        action = f"{action}{separator}q={query}"
        self._post_form(
            action,
            {"q": query, "t:formdata": chosen["t_formdata"]},
            extra_headers={
                "Referer": f"{_BASE}/es/search/results/?q={query}",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        return self.read_cart()

    def read_cart(self) -> EroskiCart:
        html = self._get_html(
            "/es/mycart/", basketType="ALI"
        )
        return self.parse_cart(html)

    def remove_item(self, product_id: str) -> EroskiCart:
        """Remove one item via its basketadditemcomponent:addtocart event."""
        html = self._get_html("/es/mycart/", basketType="ALI")
        token_match = _FORMDATA_RE.search(html)
        if not token_match:
            raise ProviderError("Eroski mycart exposed no t:formdata token")
        action = "/es/mycart.basket.productlist.basketproduct.basketadditemcomponent:addtocart"
        self._post_form(action, {"product": product_id, "t:formdata": token_match.group(1)})
        return self.read_cart()

    def status(self) -> dict[str, Any]:
        return {
            "store": "eroski",
            "state_path": str(self.state_path),
            "session_present": self.state_path.is_file(),
            "http_backend": "eroski_http",
            "profile_values_exposed": False,
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["EroskiCart", "EroskiCartItem", "EroskiHTTPClient"]
