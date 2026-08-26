"""Playwright helpers driving the rendered Eroski storefront session.

Used for cart writes while the Tapestry zone binding is replicated in pure
HTTP. Reads stay on ``eroski_http`` (no browser needed).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from open_grocery_mcp.providers.browser_normalize import (
    is_restricted_product,
    parse_money_text,
)

_BASE = "https://supermercado.eroski.es"


def _session_storage_sidecar(state_path: str):
    import json as _json
    from pathlib import Path as _Path

    side = _Path(state_path).parent / "session_storage.json"
    try:
        payload = _json.loads(side.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def ui_context(state_path: str):
    from playwright.sync_api import sync_playwright

    pm = sync_playwright().start()
    browser = pm.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=str(state_path),
        locale="es-ES",
        viewport={"width": 1440, "height": 1000},
    )
    stored = _session_storage_sidecar(state_path)
    if stored:
        payload = json.dumps(stored)
        context.add_init_script(
            "(() => { try { const d = "
            + payload.replace("'", "\'")
            + "; for (const [o, entries] of Object.entries(d)) {"
            " if (location.origin !== o) continue;"
            " for (const [k, v] of Object.entries(entries)) {"
            " if (!sessionStorage.getItem(k)) sessionStorage.setItem(k, v); } } }"
            " catch(e){} })()"
        )
    page = context.new_page()
    page.set_default_timeout(45000)

    def goto(url: str) -> bool:
        for wait in ("domcontentloaded", "commit"):
            try:
                page.goto(url, wait_until=wait, timeout=45000)
                return True
            except Exception:
                continue
        return False

    return {
        "pm": pm,
        "browser": browser,
        "page": page,
        "goto": goto,
        "close": lambda: (browser.close(), pm.stop()),
    }


def header_total(page) -> str:
    selector = (
        ".shopping-cart__totalprice .price"
    )
    return page.evaluate(
        "(() => { const e = document.querySelector('" + selector + "');"
        " return e ? e.innerText.trim() : ''; })()"
    )


_PRODUCT_REF_RE = re.compile(r"/(?:productdetail|product)/([A-Za-z0-9_-]+)", re.I)
_ITEM_LIST_REF_RE = re.compile(r"^item-list-([A-Za-z0-9_-]+)$", re.I)


def _product_tile(link: Any) -> Any:
    """Find the product card, using an exact ``product-item`` class token."""

    return link.locator(
        "xpath=ancestor::article[1] | ancestor::li[1] | "
        "ancestor::div[contains(concat(' ', normalize-space(@class), ' '), "
        "' product-item ')][1]"
    )


def _tile_product_ref(tile: Any) -> str | None:
    """Extract the rendered card's product identity without trusting its index."""

    # The storefront may put the identity on the product-item root rather than
    # on a descendant.  Check the root first; Playwright descendant locators
    # do not include their owning element.
    for attribute in (
        "data-product-ref",
        "data-productref",
        "data-product-id",
        "id",
    ):
        try:
            value = tile.get_attribute(attribute)
        except Exception:
            value = None
        if not value:
            continue
        item_match = _ITEM_LIST_REF_RE.fullmatch(str(value).strip())
        if item_match:
            return item_match.group(1)
        match = _PRODUCT_REF_RE.search(str(value))
        if match:
            return match.group(1).strip()
        if attribute != "id":
            return str(value).strip() or None

    selectors = (
        "[data-product-ref]",
        "[data-productref]",
        "[data-product-id]",
        "[id^='item-list-']",
        "a.product-title-link",
    )
    for selector in selectors:
        try:
            candidates = tile.locator(selector)
            for index in range(min(candidates.count(), 5)):
                candidate = candidates.nth(index)
                for attribute in (
                    "data-product-ref",
                    "data-productref",
                    "data-product-id",
                    "id",
                    "href",
                ):
                    value = candidate.get_attribute(attribute)
                    if not value:
                        continue
                    item_match = _ITEM_LIST_REF_RE.fullmatch(str(value).strip())
                    if item_match:
                        return item_match.group(1)
                    match = _PRODUCT_REF_RE.search(str(value))
                    if match:
                        return match.group(1).strip()
                    if attribute not in {"href", "id"}:
                        return str(value).strip() or None
        except Exception:
            continue
    return None


def add_first_result(
    ui: dict,
    query: str = "leche",
    *,
    tile_index: int = 0,
    max_price: Decimal = Decimal("5.00"),
    expected_product_ref: str | None = None,
) -> dict[str, Any]:
    expected_ref = str(expected_product_ref or "").strip() or None
    if is_restricted_product(query):
        return {
            "added": False,
            "write_attempted": False,
            "reason": "age-restricted product blocked",
        }
    page = ui["page"]
    if not ui["goto"](f"{_BASE}/es/search/results/?q={query}"):
        return {"added": False, "write_attempted": False, "reason": "navigation failed"}
    page.wait_for_timeout(5000)
    before_total = header_total(page)
    links = page.locator("a.update.toAddProduct")
    price: Decimal | None = None
    write_attempted = False
    try:
        count = links.count()
        if expected_ref:
            link = None
            tile = None
            for index in range(min(count, 100)):
                candidate = links.nth(index)
                candidate_tile = _product_tile(candidate)
                if not candidate_tile.count():
                    continue
                if _tile_product_ref(candidate_tile.first) == expected_ref:
                    link = candidate
                    tile = candidate_tile
                    break
            if link is None or tile is None:
                return {
                    "added": False,
                    "write_attempted": False,
                    "reason": "expected product reference not found in rendered tiles",
                }
        else:
            if tile_index < 0 or tile_index >= count:
                return {"added": False, "write_attempted": False, "reason": "no toAddProduct control"}
            link = links.nth(tile_index)
            tile = _product_tile(link)
        if not link.is_visible():
            return {"added": False, "write_attempted": False, "reason": "toAddProduct control is hidden"}
        if tile.count() == 0:
            return {"added": False, "write_attempted": False, "reason": "product tile could not be inspected"}
        tile_text = tile.first.inner_text()
        if is_restricted_product(tile_text):
            return {"added": False, "write_attempted": False, "reason": "age-restricted product blocked"}
        price = parse_money_text(tile_text)
        if price <= 0:
            return {"added": False, "write_attempted": False, "reason": "ordinary product price unavailable"}
        if price > max_price:
            return {
                "added": False,
                "write_attempted": False,
                "reason": "ordinary product price exceeds probe cap",
                "price": f"{price:.2f}",
            }
        write_attempted = True
        link.click()
    except Exception as exc:
        result: dict[str, Any] = {
            "added": False,
            "write_attempted": write_attempted,
            "reason": type(exc).__name__,
        }
        if price is not None and price > 0:
            result["product_price"] = f"{price:.2f}"
        return result
    page.wait_for_timeout(4000)
    after_total = header_total(page)
    if not after_total or after_total == before_total:
        return {
            "added": False,
            "write_attempted": write_attempted,
            "reason": "cart total did not change after the click",
            "header_total": after_total,
            "product_price": f"{price:.2f}",
        }
    return {
        "added": True,
        "write_attempted": write_attempted,
        "header_total": after_total,
        "product_price": f"{price:.2f}",
    }


def remove_product(
    ui: dict, product_id: str, *, max_clicks: int = 6
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(product_id)):
        return {"removed": False, "reason": "invalid product id"}
    if isinstance(max_clicks, bool) or not isinstance(max_clicks, int) or max_clicks < 1:
        return {"removed": False, "reason": "invalid removal limit"}
    page = ui["page"]
    url = _BASE + "/es/mycart/?basketType=ALI"
    if not ui["goto"](url):
        return {"removed": False, "reason": "navigation failed"}
    page.wait_for_timeout(4000)
    removed = 0
    row_selector = (
        'div.row.shopping-cart-item:has([class*="basket-product-'
        + product_id
        + '"])'
    )
    for _ in range(max_clicks):
        rows = page.locator(row_selector)
        if rows.count() == 0:
            break
        link = rows.first.locator("a.remove-item-shopping-btn-cart")
        if link.count() == 0 or not link.first.is_visible():
            break
        link.first.click()
        removed += 1
        page.wait_for_timeout(3500)
    rows_left = page.locator(row_selector).count()
    return {
        "removed": bool(removed and rows_left == 0),
        "removed_clicks": removed,
        "header_total": header_total(page),
        "rows_left": rows_left,
    }
