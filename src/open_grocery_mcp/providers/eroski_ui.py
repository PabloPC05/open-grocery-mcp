"""Playwright helpers driving the rendered Eroski storefront session.

Used for cart writes while the Tapestry zone binding is replicated in pure
HTTP. Reads stay on ``eroski_http`` (no browser needed).
"""

from __future__ import annotations

import json
from typing import Any

_BASE = "https://supermercado.eroski.es"


def _session_storage_sidecar(state_path: str):
    import json as _json
    from pathlib import Path as _Path

    side = _Path(state_path).parent / "session_storage.json"
    try:
        return _json.loads(side.read_text(encoding="utf-8"))
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
            " if (!location.origin.includes(o)) continue;"
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

    def dump_session_storage() -> dict:
        raw = page.evaluate("(() => { const o={}; for (let i=0;i<sessionStorage.length;i++){const k=sessionStorage.key(i); o[k]=sessionStorage.getItem(k);} return o; })()")
        return {page.evaluate("location.origin"): raw}

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


def add_first_result(ui: dict, query: str = "leche") -> dict[str, Any]:
    page = ui["page"]
    if not ui["goto"](f"{_BASE}/es/search/results/?q={query}"):
        return {"added": False, "reason": "navigation failed"}
    page.wait_for_timeout(5000)
    link = page.locator("a.update.toAddProduct").first
    try:
        if link.count() == 0 or not link.is_visible():
            return {"added": False, "reason": "no toAddProduct control"}
        link.click()
    except Exception as exc:
        return {"added": False, "reason": type(exc).__name__}
    page.wait_for_timeout(4000)
    return {"added": True, "header_total": header_total(page)}


def remove_product(ui: dict, product_id: str) -> dict[str, Any]:
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
    for _ in range(6):
        rows = page.locator(row_selector)
        if rows.count() == 0:
            break
        link = rows.first.locator("a.remove-item-shopping-btn-cart")
        if link.count() == 0 or not link.first.is_visible():
            break
        link.first.click()
        removed += 1
        page.wait_for_timeout(3500)
    return {
        "removed_clicks": removed,
        "header_total": header_total(page),
        "rows_left": page.locator(row_selector).count(),
    }
