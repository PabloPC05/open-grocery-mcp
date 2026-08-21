"""Pure helpers for the live reversible Gadis cart verifier."""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Mapping

from open_grocery_mcp.models import money
from open_grocery_mcp.providers.browser_normalize import is_restricted_product
from open_grocery_mcp.providers.gadis import GadisProvider

MAX_ADDED_VALUE = Decimal("5.00")
ORDER_OPT_INS = (
    "OPEN_GROCERY_ENABLE_ORDER_SUBMISSION",
    "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION",
)


def enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def decimal_value(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception:
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def cart_lines(cart: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = cart.get("lines", [])
    return [line for line in raw if isinstance(line, Mapping)] if isinstance(raw, list) else []


def line_signature(cart: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for line in cart_lines(cart):
        product_id = str(line.get("product_id", "")).strip()
        quantity = decimal_value(line.get("quantity")).normalize()
        if product_id and quantity > 0:
            result.append((product_id, str(quantity)))
    return tuple(sorted(result))


def cart_fingerprint(
    cart: Mapping[str, Any],
) -> tuple[tuple[tuple[str, str], ...], str, int]:
    return (
        line_signature(cart),
        str(decimal_value(cart.get("total")).quantize(Decimal("0.01"))),
        len(cart_lines(cart)),
    )


def product_ids(cart: Mapping[str, Any]) -> set[str]:
    return {
        str(line.get("product_id", "")).strip()
        for line in cart_lines(cart)
        if str(line.get("product_id", "")).strip()
    }


def quantity(cart: Mapping[str, Any], product_id: str) -> Decimal:
    for line in cart_lines(cart):
        if str(line.get("product_id", "")).strip() == product_id:
            return decimal_value(line.get("quantity"))
    return Decimal("0")


def require_http_cart(workflows: Any) -> dict[str, Any]:
    cart = workflows.real_cart("gadis")
    if cart.get("cart_backend") != "gadis_http" or cart.get("browser_driven") is not False:
        raise RuntimeError("Gadis cart did not use the authenticated HTTP backend")
    if not cart.get("version"):
        raise RuntimeError("Gadis HTTP cart did not expose a version")
    return cart


def select_safe_product(
    excluded_ids: set[str],
    max_added_value: Decimal,
    store_id: str | None,
) -> dict[str, Any]:
    """Choose an absent product whose quantity-two peak remains under the cap."""
    unit_cap = (max_added_value / 2).quantize(Decimal("0.01"))
    provider = GadisProvider(store_id=store_id)
    try:
        for query in (
            "agua mineral 1 l",
            "leche entera 1 l",
            "arroz 1 kg",
            "sal fina 1 kg",
        ):
            for product in provider.search(query, limit=20):
                if not product.id or product.id in excluded_ids or not product.available:
                    continue
                if not (Decimal("0") < product.price <= unit_cap):
                    continue
                if is_restricted_product(product.name, product.category or ""):
                    continue
                return {
                    "product_id": product.id,
                    "name": product.name,
                    "price": product.price,
                    "category": product.category or "",
                }
    finally:
        provider.close()
    raise RuntimeError("no absent, non-restricted Gadis product fits the test cap")


def basket_for_product(product: Mapping[str, Any], quantity_value: int) -> dict[str, Any]:
    price = decimal_value(product.get("price"))
    total = price * quantity_value
    return {
        "store": "gadis",
        "label": "Gadis",
        "currency": "EUR",
        "total": float(total),
        "total_text": money(total),
        "complete": True,
        "required_missing": 0,
        "details": [
            {
                "request": {
                    "query": "reversible local verification product",
                    "quantity": quantity_value,
                    "required": True,
                },
                "found": True,
                "product": {
                    "id": str(product["product_id"]),
                    "name": str(product["name"]),
                    "price": float(price),
                    "category": str(product.get("category", "")),
                },
            }
        ],
    }
