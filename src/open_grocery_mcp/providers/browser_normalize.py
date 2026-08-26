"""Pure normalizers used by the browser workflow.

Keeping network/DOM normalization pure makes it testable without installing a
browser in CI and lets the Playwright layer use captured JSON when available,
falling back to the rendered page only when necessary.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from open_grocery_mcp.models import as_decimal, money

_MONEY_RE = re.compile(r"(?<!\d)(\d{1,6}(?:[.,]\d{1,2})?)\s*€", re.I)
_RESTRICTED_RE = re.compile(
    r"\b(?:cerveza|vino|whisk(?:y|ey)|vodka|ginebra|ron|licor|brandy|cava|sidra|"
    r"champagne|tequila|mezcal|vermut|vermouth|bourbon|co(?:ñ|n)ac|aguardiente|"
    r"pachar(?:a|á)n|sangr(?:i|í)a|alcohol(?:ico|ica|icos|icas)?|tabaco|"
    r"cigarr(?:o|os|illo|illos)|vape|nicotina)\b",
    re.I,
)


def normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().casefold()


def sanitize_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.scheme:
        return raw
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse_money_text(text: Any) -> Decimal:
    values = [as_decimal(match) for match in _MONEY_RE.findall(str(text or ""))]
    positives = [value for value in values if value > 0]
    return positives[-1] if positives else Decimal("0")


def is_restricted_product(name: Any, category: Any = None) -> bool:
    return bool(_RESTRICTED_RE.search(f"{name or ''} {category or ''}"))


def same_line_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Return whether two retailer/cart lines identify the same product.

    Browser-rendered carts do not always expose the catalogue product ID, so the
    comparison falls back to a sanitized product URL and finally a conservative
    normalized-name comparison.
    """

    left_id = str(left.get("product_id") or left.get("id") or "").strip()
    right_id = str(right.get("product_id") or right.get("id") or "").strip()
    if left_id and right_id and left_id == right_id:
        return True

    left_url = sanitize_url(left.get("url"))
    right_url = sanitize_url(right.get("url"))
    if left_url and right_url:
        left_path = urlsplit(left_url).path.rstrip("/")
        right_path = urlsplit(right_url).path.rstrip("/")
        if left_path and right_path and left_path == right_path:
            return True

    left_name = normalized_text(left.get("name"))
    right_name = normalized_text(right.get("name"))
    if not left_name or not right_name:
        return False
    # Exact names are safest. Containment is allowed only for reasonably long
    # names, avoiding accidental matches such as "pan" vs "pan rallado".
    if left_name == right_name:
        return True
    shorter, longer = sorted((left_name, right_name), key=len)
    return len(shorter) >= 12 and shorter in longer


def canonical_line_key(line: Mapping[str, Any]) -> str:
    product_id = str(line.get("product_id") or line.get("id") or "").strip()
    if product_id:
        return f"id:{product_id}"
    url = sanitize_url(line.get("url"))
    if url:
        return f"url:{normalized_text(url)}"
    name = normalized_text(line.get("name"))
    return f"name:{name}"


def cart_version(lines: Iterable[Mapping[str, Any]], total: Decimal | float | str) -> int:
    normalized = [
        {
            "key": canonical_line_key(line),
            "quantity": str(as_decimal(line.get("quantity"))),
            "unit_price": str(as_decimal(line.get("unit_price"))),
        }
        for line in lines
    ]
    normalized.sort(key=lambda item: item["key"])
    payload = json.dumps(
        {"lines": normalized, "total": str(as_decimal(total))},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & 0x7FFFFFFF
    return value or 1


def _nested(mapping: Mapping[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = mapping
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                value = None
                break
            value = value[key]
        if value not in (None, ""):
            return value
    return None


def _line_from_raw(raw: Mapping[str, Any], store: str) -> dict[str, Any] | None:
    product = raw.get("product") if isinstance(raw.get("product"), Mapping) else {}
    price_info = product.get("price_instructions") if isinstance(product.get("price_instructions"), Mapping) else {}
    product_id = str(
        raw.get("product_id")
        or raw.get("sku")
        or raw.get("code")
        or product.get("id")
        or raw.get("id")
        or ""
    ).strip()
    name = str(
        product.get("display_name")
        or product.get("name")
        or raw.get("display_name")
        or raw.get("name")
        or raw.get("title")
        or ""
    ).strip()
    quantity = as_decimal(
        raw.get("quantity")
        if raw.get("quantity") is not None
        else raw.get("qty")
        if raw.get("qty") is not None
        else raw.get("units")
        if raw.get("units") is not None
        else raw.get("count"),
        default="1",
    )
    unit_price = as_decimal(
        raw.get("unit_price")
        or price_info.get("unit_price")
        or (product.get("price") if not isinstance(product.get("price"), Mapping) else None)
        or (raw.get("price") if not isinstance(raw.get("price"), Mapping) else None)
    )
    url = sanitize_url(
        product.get("share_url")
        or product.get("url")
        or raw.get("share_url")
        or raw.get("url")
    )
    if not product_id and not name and not url:
        return None
    if quantity <= 0:
        return None
    line_total = unit_price * quantity
    return {
        "store": store,
        "product_id": product_id,
        "name": name,
        "quantity": float(quantity),
        "unit_price": float(unit_price),
        "unit_price_text": money(unit_price),
        "line_total": float(line_total),
        "line_total_text": money(line_total),
        "url": url,
    }


def _walk(value: Any, *, depth: int = 0) -> Iterable[Mapping[str, Any]]:
    if depth > 8:
        return
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, depth=depth + 1)


def normalize_cart_payload(payload: Any, store: str) -> dict[str, Any] | None:
    """Find and normalize the most cart-like object in an arbitrary response."""

    candidates: list[tuple[int, dict[str, Any]]] = []
    line_keys = ("lines", "items", "products", "cart_items", "cartItems", "entries")
    for mapping in _walk(payload):
        raw_lines: Any = None
        chosen_key = ""
        for key in line_keys:
            if isinstance(mapping.get(key), list):
                raw_lines = mapping[key]
                chosen_key = key
                break
        if raw_lines is None:
            continue
        lines = [
            line
            for item in raw_lines
            if isinstance(item, Mapping)
            for line in [_line_from_raw(item, store)]
            if line is not None
        ]
        total = as_decimal(
            _nested(
                mapping,
                ("summary", "total"),
                ("totals", "grand_total"),
                ("price", "total"),
                ("total",),
                ("grand_total",),
                ("amount",),
            )
        )
        if total <= 0 and lines:
            total = sum((as_decimal(line["line_total"]) for line in lines), Decimal("0"))
        score = len(lines) * 10
        score += 5 if chosen_key in {"lines", "cart_items", "cartItems"} else 0
        score += 4 if total > 0 else 0
        score += 2 if any(key in mapping for key in ("cart", "version", "products_count", "summary")) else 0
        if not lines and total <= 0:
            continue
        version_raw = mapping.get("version")
        try:
            version = int(version_raw) if version_raw not in (None, "") else cart_version(lines, total)
        except (TypeError, ValueError):
            version = cart_version(lines, total)
        normalized = {
            "store": store,
            "cart_id": str(mapping.get("id") or mapping.get("cart_id") or ""),
            "version": version,
            "products_count": int(mapping.get("products_count") or len(lines)),
            "total": float(total),
            "total_text": money(total),
            "currency": str(mapping.get("currency") or "EUR"),
            "lines": lines,
        }
        candidates.append((score, normalized))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def normalize_dom_cart(payload: Mapping[str, Any], store: str) -> dict[str, Any]:
    raw_lines = payload.get("lines", []) if isinstance(payload, Mapping) else []
    lines = [
        line
        for raw in raw_lines if isinstance(raw, Mapping)
        for line in [_line_from_raw(raw, store)]
        if line is not None
    ]
    total = as_decimal(payload.get("total"))
    if total <= 0:
        total = parse_money_text(payload.get("text"))
    if total <= 0:
        total = sum((as_decimal(line["line_total"]) for line in lines), Decimal("0"))
    return {
        "store": store,
        "cart_id": str(payload.get("cart_id") or ""),
        "version": cart_version(lines, total),
        "products_count": len(lines),
        "total": float(total),
        "total_text": money(total),
        "currency": "EUR",
        "lines": lines,
    }


def normalize_addresses(payload: Any) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for mapping in _walk(payload):
        address_id = mapping.get("address_id") or mapping.get("id") or mapping.get("uuid")
        postal = mapping.get("postal_code") or mapping.get("postcode") or mapping.get("zip") or mapping.get("cp")
        city = mapping.get("city") or mapping.get("town") or mapping.get("locality") or mapping.get("municipality")
        street = mapping.get("street") or mapping.get("address") or mapping.get("line1") or mapping.get("address_line")
        if address_id in (None, "") or not any((postal, city, street)):
            continue
        key = str(address_id)
        label_parts = [str(part).strip() for part in (postal, city) if str(part or "").strip()]
        result[key] = {
            "id": key,
            "label": " · ".join(label_parts) or "Dirección guardada",
            "postal_code": str(postal or ""),
            "city": str(city or ""),
            "street_redacted": True,
            "default": bool(mapping.get("default") or mapping.get("is_default") or mapping.get("selected")),
        }
    return list(result.values())


def normalize_slots(payload: Any) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for mapping in _walk(payload):
        slot_id = mapping.get("slot_id") or mapping.get("id") or mapping.get("uuid")
        start = mapping.get("start") or mapping.get("start_date") or mapping.get("from")
        end = mapping.get("end") or mapping.get("end_date") or mapping.get("to")
        label = mapping.get("label") or mapping.get("name") or mapping.get("description")
        if slot_id in (None, "") or not any((start, end, label)):
            continue
        available = mapping.get("available")
        if available is None:
            available = not bool(mapping.get("disabled") or mapping.get("sold_out"))
        price = as_decimal(mapping.get("price") or mapping.get("fee") or mapping.get("cost"))
        key = str(slot_id)
        result[key] = {
            "id": key,
            "start": str(start or ""),
            "end": str(end or ""),
            "label": str(label or "").strip(),
            "available": bool(available),
            "open": bool(mapping.get("open", True)),
            "price": float(price),
            "price_text": money(price),
        }
    return list(result.values())


def extract_order_id(payload: Any) -> str | None:
    for mapping in _walk(payload):
        for key in ("order_id", "orderId", "order_number", "orderNumber"):
            value = str(mapping.get(key) or "").strip()
            if value:
                return value
    return None
