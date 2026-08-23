"""Authenticated Eroski HTTP client built from the verified Tapestry contract.

Eroski's storefront is a server-rendered Apache Tapestry 5 application: there
is no JSON API, so this client drives the same forms the browser uses.

Verified live contract (value-free):

- session cookies come from the saved Playwright ``storage_state``;
  ``GET /?zipCode=<cp>`` establishes the delivery context;
- ``GET /es/search/results/?q=<term>`` embeds one
  ``["common/button/productListItemAddComponent:init", {...}]`` config blob
  per result tile carrying ``productRef``, ``shopRef``, ``quantityInCart``,
  the ``onAddToCartEvent`` URL and ``t:formdata`` tokens;
- adding posts URL-encoded to that event URL with ``q``, ``t:formdata``,
  ``product=<json>`` (productRef/shopRef/selectionType/unitsToAdd/
  newQuantity/...) and one ``<zoneName>=<zoneElementId>`` pair per refresh
  zone present on the page;
- ``GET /es/mycart/?basketType=ALI`` renders rows
  ``div.row.shopping-cart-item`` with ``[class*=basket-product-{pid}]`` and a
  quantity input; removal repeats the add event with ``newQuantity: 0``.

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

_INIT_MARKER = "productListItemAddComponent:init"
_FORMDATA_RE = re.compile(
    r'<input[^>]*?name="t:formdata"[^>]*?value="([^"]*)"'
    r'|<input[^>]*?value="([^"]*)"[^>]*?name="t:formdata"',
    re.S | re.I,
)
_QTY_RE = re.compile(r'class="[^"]*quantity[^"]*"[^>]*value="([0-9]+)"', re.S)
_PRODUCT_ID_RE = re.compile(r"basket-product-(\d+)")
_TOTAL_RE = re.compile(
    r'class="shopping-cart__totalprice[^"]*".*?class="price"[^>]*>\s*([0-9,.]+)',
    re.S,
)
_ZONES = (
    "basketTotalPriceZone",
    "sectionZoneALI",
    "sectionZoneELECTRO",
    "sectionZoneDESCANSO",
    "sectionZoneMARKETPLACE",
    "summaryMobileZone",
    "dontReplaceZoneAll",
    "summaryZone",
)


@dataclass
class TileConfig:
    """Per-result component config embedded by the storefront."""

    item_id: str
    product_ref: str
    shop_ref: str | None
    previous_address_ref: str | None
    quantity_in_cart: int
    maximum_quantity: int
    product_units_per_pack: int
    is_weight_options_available: bool
    on_add_to_cart_event: str
    raw: dict[str, Any] = field(default_factory=dict)


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
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") >> 1


def _balanced_json(source: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return None


def _extract_zone_fields(html: str) -> dict[str, str]:
    zones: dict[str, str] = {}
    for zone in _ZONES:
        if f'id="{zone}"' in html:
            zones[zone] = zone
    return zones


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
        self.state_path = (
            Path(state_path).expanduser()
            if state_path
            else _default_state_path()
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
        response = self._client.get(_BASE + "/", params={"zipCode": self.zip_code})
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
            response = self._client.get(_BASE + path, params=params or None)
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
        referer: str | None = None,
    ) -> str:
        url = urljoin(_BASE + "/", action_url)
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if referer:
            headers["Referer"] = referer
        try:
            response = self._client.post(url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Eroski POST failed: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"Eroski POST {url[-80:]} returned HTTP {response.status_code}"
            )
        return response.text

    # ---------------------------------------------------------------- parsing

    @staticmethod
    def parse_cart(html: str) -> EroskiCart:
        total_match = _TOTAL_RE.search(html)
        total_text = (
            total_match.group(1) + "€" if total_match else "0,00€"
        )
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
    def parse_tile_configs(html: str) -> list[tuple[TileConfig, dict[str, str]]]:
        """Extract every tile config plus the page-level t:formdata."""
        token_match = _FORMDATA_RE.search(html)
        token = (token_match.group(1) or token_match.group(2)) if token_match else ""
        configs: list[tuple[TileConfig, dict[str, str]]] = []
        position = 0
        while True:
            idx = html.find(_INIT_MARKER, position)
            if idx < 0:
                break
            brace = html.find("{", idx)
            if brace < 0:
                break
            blob = _balanced_json(html, brace)
            position = idx + len(_INIT_MARKER)
            if not blob:
                continue
            try:
                raw = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, Mapping):
                continue
            ref = str(raw.get("productRef") or "").strip()
            event = str(raw.get("onAddToCartEvent") or "").strip()
            if not ref or not event:
                continue
            config = TileConfig(
                item_id=str(raw.get("itemId") or ""),
                product_ref=ref,
                shop_ref=str(raw.get("shopRef") or "") or None,
                previous_address_ref=(
                    str(raw.get("previousAddressRef") or "") or None
                ),
                quantity_in_cart=int(raw.get("quantityInCart") or 0),
                maximum_quantity=int(raw.get("maximumQuantity") or 99),
                product_units_per_pack=int(raw.get("productUnitsPerPack") or 1),
                is_weight_options_available=bool(
                    raw.get("isWeightOptionsAvailable")
                ),
                on_add_to_cart_event=event,
                raw=dict(raw),
            )
            configs.append((config, {"t_formdata": token}))
        return configs

    # ------------------------------------------------------------ operations

    def search_tiles(self, query: str) -> list[TileConfig]:
        html = self._get_html("/es/search/results/", q=query)
        return [config for config, _ in self.parse_tile_configs(html)]

    def read_cart(self) -> EroskiCart:
        html = self._get_html("/es/mycart/", basketType="ALI")
        return self.parse_cart(html)

    def add_to_cart(
        self,
        query: str,
        *,
        tile_index: int = 0,
        quantity: int = 1,
    ) -> EroskiCart:
        html = self._get_html("/es/search/results/", q=query)
        parsed = self.parse_tile_configs(html)
        if tile_index >= len(parsed):
            raise ProviderError(
                f"Eroski search {query!r} rendered only {len(parsed)} tiles"
            )
        config, page_data = parsed[tile_index]
        token = page_data.get("t_formdata") or ""
        if not token:
            raise ProviderError("search page exposed no t:formdata token")

        new_quantity = min(
            config.quantity_in_cart + quantity, config.maximum_quantity
        )
        product_payload = {
            "productRef": config.product_ref,
            "shopRef": config.shop_ref,
            "previousAddressRef": config.previous_address_ref,
            "productUnitsPerPack": config.product_units_per_pack,
            "selectionType": "weight"
            if config.is_weight_options_available
            else "ud",
            "unitsToAdd": new_quantity - config.quantity_in_cart,
            "newQuantity": new_quantity,
            "checkedPendingOrder": False,
            "isPickupChecked": False,
        }
        data = {
            "q": query,
            "t:formdata": token,
            "product": json.dumps(product_payload, separators=(",", ":")),
        }
        data.update(_extract_zone_fields(html))
        referer = f"{_BASE}/es/search/results/?q={query}"
        self._post_form(config.on_add_to_cart_event, data, referer=referer)
        return self.read_cart()

    def set_item_quantity(
        self, query_or_page: str, product_id: str, quantity: int
    ) -> EroskiCart:
        """Set an existing cart item's quantity via its own tile event."""
        source = (
            self._get_html("/es/mycart/", basketType="ALI")
            if query_or_page == "@cart"
            else self._get_html("/es/search/results/", q=query_or_page)
        )
        parsed = self.parse_tile_configs(source)
        target = next(
            (cfg for cfg, _ in parsed if cfg.product_ref == product_id),
            None,
        )
        if target is None:
            raise ProviderError(f"Eroski tile for {product_id} not found")
        token = next((d.get("t_formdata") or "" for _, d in parsed), "")
        current = max(target.quantity_in_cart, 0)
        new_quantity = max(0, min(quantity, target.maximum_quantity))
        product_payload = {
            "productRef": target.product_ref,
            "shopRef": target.shop_ref,
            "previousAddressRef": target.previous_address_ref,
            "productUnitsPerPack": target.product_units_per_pack,
            "selectionType": "weight"
            if target.is_weight_options_available
            else "ud",
            "unitsToAdd": new_quantity - current,
            "newQuantity": new_quantity,
            "checkedPendingOrder": False,
            "isPickupChecked": False,
        }
        data = {
            "q": "",
            "t:formdata": token,
            "product": json.dumps(product_payload, separators=(",", ":")),
        }
        data.update(_extract_zone_fields(source))
        self._post_form(target.on_add_to_cart_event, data)
        return self.read_cart()

    def remove_item(self, product_id: str) -> EroskiCart:
        return self.set_item_quantity("@cart", product_id, 0)

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


def _default_state_path() -> Path:
    configured = os.getenv("OPEN_GROCERY_EROSKI_STATE_PATH")
    if configured:
        return Path(configured).expanduser()
    root = Path(
        os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")
    ).expanduser()
    return root / "eroski" / "storage_state.json"


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


__all__ = ["EroskiCart", "EroskiCartItem", "EroskiHTTPClient", "TileConfig"]
