"""Shared helpers for value-free supermarket HTTP-contract capture."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, unquote, urlsplit, urlunsplit

from open_grocery_mcp.providers.froiz import FroizProvider
from open_grocery_mcp.providers.gadis import GadisProvider

SENSITIVE = re.compile(
    r"(?i)(pass|secret|token|auth|cookie|csrf|xsrf|session|email|phone|mobile|"
    r"address|street|postal|zip|first.?name|last.?name|surname|dni|nif|card|"
    r"iban|bic|cvv|cvc|birth|customer.?id|user.?id|account.?id|api.?key)"
)
RELEVANT = re.compile(
    r"(?i)(api|graphql|auth|login|session|customer|user|profile|cart|basket|cesta|"
    r"carrito|checkout|address|direccion|delivery|entrega|slot|order|pedido|store)"
)
DANGEROUS = re.compile(
    r"(?i)(/checkouts?/.*/orders?/?$|/orders?/?$|place.?order|submit.?order|"
    r"confirm.?order|payment|redsys|3ds|purchase)"
)
RESTRICTED = re.compile(
    r"(?i)\b(vino|cerveza|whisk(?:y|ey)|vodka|ginebra|ron|licor|cava|sidra|"
    r"tabaco|cigarr|vape|nicotina)\b"
)
UUID = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f-]{27,}$")
OPAQUE = re.compile(r"^[A-Za-z0-9_=-]{25,}$")
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE = re.compile(r"(?<!\d)(?:\+?34[ .-]?)?[6789](?:[ .-]?\d){8}(?!\d)")

# A short numeric value is still private when it appears after one of these
# route segments (e.g. /addresses/42/slots). Product/category/store identifiers
# are intentionally preserved because they are public catalogue identifiers and
# are needed to reproduce the HTTP contract.
PRIVATE_PATH_PARENTS = {
    "account",
    "accounts",
    "address",
    "addresses",
    "basket",
    "baskets",
    "cart",
    "carts",
    "carrito",
    "cesta",
    "checkout",
    "checkouts",
    "customer",
    "customers",
    "delivery",
    "deliveries",
    "order",
    "orders",
    "payment",
    "payments",
    "profile",
    "session",
    "sessions",
    "user",
    "users",
}
STATIC_PRIVATE_CHILDREN = {
    "actions",
    "addresses",
    "cart",
    "checkout",
    "checkouts",
    "current",
    "delivery",
    "delivery-info",
    "history",
    "items",
    "lines",
    "me",
    "orders",
    "profile",
    "recommendations",
    "slots",
    "summary",
}
PUBLIC_PATH_PARENTS = {
    "category",
    "categories",
    "product",
    "products",
    "site",
    "sites",
    "store",
    "stores",
    "warehouse",
    "warehouses",
}


@dataclass(frozen=True)
class StoreSpec:
    key: str
    label: str
    base_url: str
    cart_paths: tuple[str, ...]
    login_words: tuple[str, ...]
    cart_words: tuple[str, ...]
    add_words: tuple[str, ...]
    checkout_words: tuple[str, ...]
    remove_words: tuple[str, ...]
    username_env: str
    password_env: str


STORES = {
    "gadis": StoreSpec(
        "gadis",
        "Gadis",
        # The current public shop and account flow are served from www.
        # The legacy super.gadisline.com host is not reliably resolvable from
        # automated runners and must not be used as the capture entry point.
        "https://www.gadisline.com",
        ("/cart", "/carrito", "/cesta", "/checkout/cart"),
        ("iniciar sesión", "acceder", "mi cuenta", "identificarse"),
        ("cesta", "carrito", "mi compra"),
        ("añadir", "agregar", "comprar"),
        ("tramitar pedido", "finalizar compra", "continuar compra", "hacer pedido", "ir al pago"),
        ("eliminar", "quitar", "borrar"),
        "GADIS_TEST_USERNAME",
        "GADIS_TEST_PASSWORD",
    ),
    "froiz": StoreSpec(
        "froiz",
        "Froiz",
        "https://supermercado.froiz.com",
        ("/cart", "/cesta", "/basket", "/checkout/cart"),
        ("iniciar sesión", "acceder", "mi cuenta", "identificarse"),
        ("tu cesta", "cesta", "carrito", "mi compra"),
        ("añadir", "agregar", "comprar"),
        ("tramitar pedido", "finalizar compra", "continuar con la compra", "hacer pedido", "ir al pago"),
        ("eliminar", "quitar", "borrar"),
        "FROIZ_TEST_USERNAME",
        "FROIZ_TEST_PASSWORD",
    ),
}


def _path_segment(segment: str, previous: str = "") -> str:
    decoded = unquote(segment)
    if not decoded:
        return segment
    previous = previous.casefold()
    if previous in PRIVATE_PATH_PARENTS and decoded.casefold() not in STATIC_PRIVATE_CHILDREN:
        return "<id>"
    if previous in PUBLIC_PATH_PARENTS:
        return segment
    if EMAIL.search(decoded) or UUID.fullmatch(decoded) or OPAQUE.fullmatch(decoded):
        return "<id>"
    if re.fullmatch(r"\d{5,}", decoded):
        return "<number>"
    return segment


def safe_url(url: str) -> str:
    """Keep route structure and query names, never account-specific values."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    path_parts = parts.path.split("/")
    sanitized: list[str] = []
    previous = ""
    for part in path_parts:
        sanitized.append(_path_segment(part, previous))
        if part:
            previous = unquote(part).casefold()
    path = "/".join(sanitized)
    query = urlencode(
        [(key, "<value>") for key, _ in parse_qsl(parts.query, keep_blank_values=True)]
    )
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def shape(value: Any, key: str = "") -> Any:
    """Preserve JSON keys and primitive types while removing user values."""
    lowered = key.casefold()
    if lowered == "id":
        return "<id>"
    if SENSITIVE.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): shape(v, str(k)) for k, v in list(value.items())[:200]}
    if isinstance(value, list):
        return [shape(v, key) for v in value[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if lowered in {
            "product_id",
            "sku",
            "code",
            "currency",
            "unit",
            "status",
            "type",
            "method",
            "locale",
            "lang",
            "site_id",
            "store_id",
            "warehouse",
            "version",
        } and len(value) <= 120:
            return value
        return "<str>"
    return f"<{type(value).__name__}>"


def safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    safe_x = {
        "x-requested-with",
        "x-customer-wh",
        "x-site-id",
        "x-store-id",
        "x-locale",
        "x-lang",
    }
    for key, value in headers.items():
        low = key.casefold()
        if (
            low in {"authorization", "cookie", "set-cookie", "proxy-authorization"}
            or SENSITIVE.search(key)
        ):
            out[key] = "<redacted>"
        elif low in {"origin", "referer"}:
            out[key] = safe_url(value)
        elif low in {"content-type", "accept"} or low in safe_x:
            out[key] = value[:300]
        elif low.startswith("x-"):
            out[key] = "<value>"
    return out


def safe_message(value: str) -> str:
    text = value
    for name in (
        "GADIS_TEST_USERNAME",
        "GADIS_TEST_PASSWORD",
        "FROIZ_TEST_USERNAME",
        "FROIZ_TEST_PASSWORD",
    ):
        secret = os.getenv(name, "")
        if secret:
            text = text.replace(secret, "<redacted>")
    text = EMAIL.sub("<redacted-email>", text)
    text = PHONE.sub("<redacted-phone>", text)
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._=-]+", "Bearer <redacted>", text)
    text = re.sub(
        r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
        "<redacted-token>",
        text,
    )
    text = re.sub(
        r"(?i)[0-9a-f]{8}-[0-9a-f-]{27,}", "<redacted-id>", text
    )
    return text[:800]


def regex(words: Iterable[str]) -> re.Pattern[str]:
    return re.compile("(?:" + "|".join(re.escape(x) for x in words) + ")", re.I)


def click_words(
    page: Any,
    words: Iterable[str],
    roles: tuple[str, ...] = ("button", "link"),
) -> bool:
    pattern = regex(words)
    for role in roles:
        try:
            loc = page.get_by_role(role, name=pattern)
            for i in range(min(loc.count(), 10)):
                if loc.nth(i).is_visible():
                    loc.nth(i).click()
                    return True
        except Exception:
            pass
    try:
        loc = page.locator("button,a,[role=button]").filter(has_text=pattern)
        for i in range(min(loc.count(), 10)):
            if loc.nth(i).is_visible():
                loc.nth(i).click()
                return True
    except Exception:
        pass
    return False


def first_visible(locator: Any) -> Any | None:
    try:
        for i in range(min(locator.count(), 20)):
            if locator.nth(i).is_visible():
                return locator.nth(i)
    except Exception:
        pass
    return None


def _browser_product_url(store: str, value: str) -> str:
    """Return the public catalogue URL used by the current storefront."""
    del store
    return value


def choose_product(store: str) -> dict[str, Any]:
    provider = GadisProvider() if store == "gadis" else FroizProvider()
    try:
        for query in ("leche entera 1 l", "arroz 1 kg", "agua mineral"):
            for product in provider.search(query, limit=10):
                if product.url and product.price > 0 and not RESTRICTED.search(product.name):
                    return {
                        "id": product.id,
                        "name": product.name,
                        "url": _browser_product_url(store, product.url),
                        "price": float(product.price),
                    }
    finally:
        provider.close()
    raise RuntimeError(f"no safe product found for {store}")
