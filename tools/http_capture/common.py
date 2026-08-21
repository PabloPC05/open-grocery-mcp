"""Shared helpers for sanitized supermarket HTTP-contract capture."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from open_grocery_mcp.providers.froiz import FroizProvider
from open_grocery_mcp.providers.gadis import GadisProvider

SENSITIVE = re.compile(
    r"(?i)(pass|secret|token|auth|cookie|csrf|xsrf|session|email|phone|mobile|"
    r"address|street|postal|zip|first.?name|last.?name|surname|dni|nif|card|"
    r"iban|bic|cvv|cvc|birth|customer.?id|user.?id|account.?id)"
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
        "gadis", "Gadis", "https://www.gadisline.com",
        ("/cart", "/carrito", "/cesta", "/checkout/cart"),
        ("iniciar sesión", "acceder", "mi cuenta", "identificarse"),
        ("cesta", "carrito", "mi compra"),
        ("añadir", "agregar", "comprar"),
        ("tramitar pedido", "finalizar compra", "continuar compra", "hacer pedido", "ir al pago"),
        ("eliminar", "quitar", "borrar"),
        "GADIS_TEST_USERNAME", "GADIS_TEST_PASSWORD",
    ),
    "froiz": StoreSpec(
        "froiz", "Froiz", "https://supermercado.froiz.com",
        ("/cart", "/cesta", "/basket", "/checkout/cart"),
        ("iniciar sesión", "acceder", "mi cuenta", "identificarse"),
        ("tu cesta", "cesta", "carrito", "mi compra"),
        ("añadir", "agregar", "comprar"),
        ("tramitar pedido", "finalizar compra", "continuar con la compra", "hacer pedido", "ir al pago"),
        ("eliminar", "quitar", "borrar"),
        "FROIZ_TEST_USERNAME", "FROIZ_TEST_PASSWORD",
    ),
}


def safe_url(url: str) -> str:
    p = urlsplit(url)
    query = urlencode((key, "<value>") for key, _ in parse_qsl(p.query, keep_blank_values=True))
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def shape(value: Any, key: str = "") -> Any:
    """Preserve a JSON contract while removing account-specific values."""
    if SENSITIVE.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): shape(v, str(k)) for k, v in list(value.items())[:200]}
    if isinstance(value, list):
        return [shape(v, key) for v in value[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if key.casefold() in {
            "id", "product_id", "sku", "code", "currency", "unit", "status", "type",
            "method", "locale", "lang", "site_id", "store_id", "warehouse", "version",
        } and len(value) <= 120:
            return value
        return "<str>"
    return f"<{type(value).__name__}>"


def safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        low = key.casefold()
        if low in {"authorization", "cookie", "set-cookie", "proxy-authorization"} or SENSITIVE.search(key):
            out[key] = "<redacted>"
        elif low in {"content-type", "accept", "origin", "referer", "x-requested-with"} or low.startswith("x-"):
            out[key] = value[:300]
    return out


def regex(words: Iterable[str]) -> re.Pattern[str]:
    return re.compile("(?:" + "|".join(re.escape(x) for x in words) + ")", re.I)


def click_words(page: Any, words: Iterable[str], roles: tuple[str, ...] = ("button", "link")) -> bool:
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


def choose_product(store: str) -> dict[str, Any]:
    provider = GadisProvider() if store == "gadis" else FroizProvider()
    try:
        for query in ("leche entera 1 l", "arroz 1 kg", "agua mineral"):
            for product in provider.search(query, limit=10):
                if product.url and product.price > 0 and not RESTRICTED.search(product.name):
                    return {"id": product.id, "name": product.name, "url": product.url, "price": float(product.price)}
    finally:
        provider.close()
    raise RuntimeError(f"no safe product found for {store}")
