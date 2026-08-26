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
  quantity input. Some responses also expose a reusable add-component config,
  but the live cart does not do so consistently; removal therefore remains on
  the verified browser fallback in the composite provider.

Order submission is not implemented here by design: Eroski places real orders
through its order endpoint with no separate checkout step.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from open_grocery_mcp.errors import AuthenticationRequired, InvalidRequest, ProviderError
from open_grocery_mcp.providers.browser_normalize import is_restricted_product

_BASE = "https://supermercado.eroski.es"
_OFFICIAL_COOKIE_DOMAINS = {"eroski.es", "supermercado.eroski.es"}
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
_PASSWORD_INPUT_RE = re.compile(
    r'<input[^>]*\btype\s*=\s*["\']password["\']',
    re.I,
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

    @property
    def total(self) -> Decimal:
        match = re.search(r"[0-9]+(?:[.,][0-9]{1,2})?", self.total_text)
        if not match:
            return Decimal("0")
        try:
            return Decimal(match.group(0).replace(",", "."))
        except InvalidOperation:
            return Decimal("0")


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
        self._state_fingerprint: str | None = None

    # ------------------------------------------------------------------ auth

    def _load_session_cookies(self) -> list[dict[str, str]]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            raise AuthenticationRequired(
                "no saved Eroski session; run login_with_browser"
            ) from None
        if not isinstance(state, Mapping):
            raise AuthenticationRequired(
                "saved Eroski session is malformed; run login_with_browser"
            )
        jar: list[dict[str, str]] = []
        supermarket_session_found = False
        now = time.time()
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
                jar.append(
                    {
                        "name": name,
                        "value": value,
                        "domain": domain or "supermercado.eroski.es",
                        "path": path,
                    }
                )
            if (
                name == "JSESSIONID"
                and normalized_domain == "supermercado.eroski.es"
                and path == "/"
            ):
                supermarket_session_found = True
        if not supermarket_session_found:
            raise AuthenticationRequired(
                "saved Eroski session lacks the supermarket JSESSIONID; "
                "run login_with_browser"
            )
        return jar

    def _storage_state_fingerprint(self) -> str | None:
        try:
            return hashlib.sha256(self.state_path.read_bytes()).hexdigest()
        except OSError:
            return None

    @staticmethod
    def _official_cookie_domain(value: Any) -> str | None:
        domain = str(value or "").strip()
        if domain.lstrip(".").casefold() not in _OFFICIAL_COOKIE_DOMAINS:
            return None
        return domain

    def _persist_authenticated_cookies(self) -> bool:
        """Atomically persist official-domain cookies from an authenticated read."""

        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return False
        if not isinstance(state, Mapping):
            return False
        rows = state.get("cookies", [])
        if not isinstance(rows, list):
            return False

        current: list[dict[str, Any]] = []
        for cookie in self._client.cookies.jar:
            domain = self._official_cookie_domain(cookie.domain)
            name = str(cookie.name or "").strip()
            path = str(cookie.path or "/") or "/"
            value = str(cookie.value or "")
            if not domain or not name or not value or not path.startswith("/"):
                continue
            record: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
            }
            if cookie.expires is not None:
                record["expires"] = cookie.expires
            current.append(record)
        if not current:
            return False

        retained = [
            dict(row)
            for row in rows
            if isinstance(row, Mapping)
            and self._official_cookie_domain(row.get("domain")) is None
        ]
        payload = dict(state)
        payload["cookies"] = retained + current
        temporary: Path | None = None
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.state_path.parent,
                prefix=f".{self.state_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary, self.state_path)
            os.chmod(self.state_path, stat.S_IRUSR | stat.S_IWUSR)
            self._state_fingerprint = self._storage_state_fingerprint()
            return True
        except OSError:
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _raise_if_authentication_page(response: httpx.Response) -> None:
        path = urlsplit(str(response.url)).path.rstrip("/").casefold()
        if path in {"/es/login", "/es/login/only"} or _PASSWORD_INPUT_RE.search(
            response.text
        ):
            raise AuthenticationRequired(
                "Eroski session is not authenticated; run login_with_browser"
            )

    @staticmethod
    def _tapestry_redirect(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        tapestry = payload.get("_tapestry")
        if not isinstance(tapestry, Mapping):
            return None
        redirect = tapestry.get("redirectURL")
        return str(redirect) if redirect else None

    def _ensure_context(self) -> None:
        if not re.fullmatch(r"\d{5}", str(self.zip_code).strip()):
            raise InvalidRequest("Eroski HTTP context needs a five-digit postal code")
        fingerprint = self._storage_state_fingerprint()
        if self._context_ready and fingerprint == self._state_fingerprint:
            return
        self._client.cookies.clear()
        for cookie in self._load_session_cookies():
            self._client.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie["domain"],
                path=cookie["path"],
            )
        response = self._client.get(_BASE + "/", params={"zipCode": self.zip_code})
        if response.status_code != 200:
            raise ProviderError(
                f"Eroski context bootstrap returned HTTP {response.status_code}"
            )
        self._raise_if_authentication_page(response)
        self._context_ready = True
        self._state_fingerprint = fingerprint

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
        self._raise_if_authentication_page(response)
        return response.text

    def _post_form(
        self,
        action_url: str,
        data: dict[str, str],
        referer: str | None = None,
    ) -> str:
        url = urljoin(_BASE + "/", action_url)
        target = urlsplit(url)
        if (
            target.scheme != "https"
            or target.hostname != "supermercado.eroski.es"
            or not target.path.startswith("/es/")
        ):
            raise ProviderError("Eroski refused an untrusted Tapestry action URL")
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if referer:
            headers["Referer"] = referer
        try:
            response = self._client.post(url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Eroski POST failed: {exc}") from exc
        if response.status_code == 401:
            self._context_ready = False
            raise AuthenticationRequired(
                "Eroski rejected a cart write; it was not retried"
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Eroski POST {url[-80:]} returned HTTP {response.status_code}"
            )
        self._raise_if_authentication_page(response)
        redirect = self._tapestry_redirect(response)
        if redirect:
            redirect_path = urlsplit(urljoin(_BASE + "/", redirect)).path.casefold()
            if redirect_path.startswith("/es/login/delivery"):
                raise ProviderError(
                    "Eroski rejected the cart mutation because the session has no "
                    "selected delivery mode, address and slot"
                )
        return response.text

    # ---------------------------------------------------------------- parsing

    @staticmethod
    def parse_cart(html: str) -> EroskiCart:
        total_match = _TOTAL_RE.search(html)
        if total_match is None:
            raise ProviderError("Eroski cart page exposed no verifiable total")
        total_text = (
            total_match.group(1) + "€" if total_match else "0,00€"
        )
        items: list[EroskiCartItem] = []
        seen: set[str] = set()
        marker = 'class="row shopping-cart-item"'
        segments = html.split(marker)[1:]
        for segment in segments:
            window = segment[:4000]
            pid_match = _PRODUCT_ID_RE.search(window)
            if not pid_match:
                continue
            pid = pid_match.group(1)
            qty_match = _QTY_RE.search(window)
            if qty_match is None:
                raise ProviderError(
                    f"Eroski cart row {pid} exposed no verifiable quantity"
                )
            qty = int(qty_match.group(1))
            if qty <= 0:
                continue
            if pid in seen:
                raise ProviderError(f"Eroski cart contained duplicate product {pid}")
            seen.add(pid)
            items.append(EroskiCartItem(product_id=pid, quantity=qty))
        cart = EroskiCart(items=items, total_text=total_text)
        if items and cart.total <= 0:
            raise ProviderError("Eroski non-empty cart exposed no positive total")
        return cart

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
            try:
                quantity_in_cart = int(raw.get("quantityInCart", 0))
                maximum_quantity = int(raw.get("maximumQuantity", 0))
                product_units_per_pack = int(raw.get("productUnitsPerPack", 1))
            except (TypeError, ValueError):
                continue
            if (
                quantity_in_cart < 0
                or maximum_quantity <= 0
                or product_units_per_pack <= 0
            ):
                continue
            config = TileConfig(
                item_id=str(raw.get("itemId") or ""),
                product_ref=ref,
                shop_ref=str(raw.get("shopRef") or "") or None,
                previous_address_ref=(
                    str(raw.get("previousAddressRef") or "") or None
                ),
                quantity_in_cart=quantity_in_cart,
                maximum_quantity=maximum_quantity,
                product_units_per_pack=product_units_per_pack,
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
        cart = self.parse_cart(html)
        # A successful cart render proves the session, unlike a public search
        # page. Persist Set-Cookie rotations only at this authenticated point.
        self._persist_authenticated_cookies()
        return cart

    def add_to_cart(
        self,
        query: str,
        *,
        tile_index: int = 0,
        quantity: int = 1,
    ) -> EroskiCart:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise InvalidRequest("Eroski add quantity must be a positive integer")
        if tile_index < 0:
            raise InvalidRequest("Eroski tile_index cannot be negative")
        html = self._get_html("/es/search/results/", q=query)
        parsed = self.parse_tile_configs(html)
        if tile_index >= len(parsed):
            raise ProviderError(
                f"Eroski search {query!r} rendered only {len(parsed)} tiles"
            )
        config, page_data = parsed[tile_index]
        product_name = str(
            config.raw.get("productName") or config.raw.get("name") or query
        )
        product_category = str(config.raw.get("category") or "")
        if is_restricted_product(product_name, product_category):
            raise InvalidRequest(
                "automated purchase of age-restricted Eroski products is not supported"
            )
        token = page_data.get("t_formdata") or ""
        if not token:
            raise ProviderError("search page exposed no t:formdata token")

        new_quantity = config.quantity_in_cart + quantity
        if new_quantity > config.maximum_quantity:
            raise InvalidRequest(
                "requested Eroski quantity exceeds the retailer maximum"
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
        cart = self.read_cart()
        actual = next(
            (item.quantity for item in cart.items if item.product_id == config.product_ref),
            0,
        )
        if actual != new_quantity:
            raise ProviderError(
                "Eroski did not persist the requested cart quantity after the add"
            )
        return cart

    def set_item_quantity(
        self, query_or_page: str, product_id: str, quantity: int
    ) -> EroskiCart:
        """Set an existing cart item's quantity via its own tile event."""
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise InvalidRequest("Eroski cart quantity must be a non-negative integer")
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
        if quantity > target.maximum_quantity:
            raise InvalidRequest(
                "requested Eroski quantity exceeds the retailer maximum"
            )
        product_name = str(
            target.raw.get("productName") or target.raw.get("name") or ""
        )
        product_category = str(target.raw.get("category") or "")
        if quantity > target.quantity_in_cart and is_restricted_product(
            product_name, product_category
        ):
            raise InvalidRequest(
                "automated purchase of age-restricted Eroski products is not supported"
            )
        token = next((d.get("t_formdata") or "" for _, d in parsed), "")
        if not token:
            raise ProviderError("cart page exposed no t:formdata token")
        current = target.quantity_in_cart
        new_quantity = quantity
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
        cart = self.read_cart()
        actual = next(
            (item.quantity for item in cart.items if item.product_id == product_id),
            0,
        )
        if actual != new_quantity:
            raise ProviderError(
                "Eroski did not persist the requested cart quantity after the update"
            )
        return cart

    def remove_item(self, product_id: str) -> EroskiCart:
        return self.set_item_quantity("@cart", product_id, 0)

    def status(self) -> dict[str, Any]:
        checked = False
        authenticated = False
        try:
            self.read_cart()
            checked = True
            authenticated = True
        except (AuthenticationRequired, ProviderError, InvalidRequest):
            checked = True
        return {
            "store": "eroski",
            "state_path": str(self.state_path),
            "session_present": self.state_path.is_file(),
            "authenticated": authenticated,
            "http_session_checked": checked,
            "http_backend": "eroski_http",
            "profile_values_exposed": False,
        }

    def invalidate_session(self) -> None:
        self._context_ready = False
        self._state_fingerprint = None
        self._client.cookies.clear()

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


__all__ = ["EroskiCart", "EroskiCartItem", "EroskiHTTPClient", "TileConfig"]
