"""Capture a value-free inventory of storefront controls and JavaScript bundles."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from .common import STORES, choose_product, click_words, safe_message, safe_url


_DOM_SCRIPT = r"""
() => {
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const controls = Array.from(document.querySelectorAll(
    'button,a,input,select,textarea,[role="button"],[role="link"]'
  )).filter(visible).slice(0, 250).map((node) => ({
    tag: node.tagName.toLowerCase(),
    role: node.getAttribute('role') || '',
    type: node.getAttribute('type') || '',
    name: node.getAttribute('name') || '',
    id: node.id || '',
    text: (node.innerText || node.textContent || '').trim().slice(0, 160),
    aria_label: (node.getAttribute('aria-label') || '').slice(0, 160),
    title: (node.getAttribute('title') || '').slice(0, 160),
    placeholder: (node.getAttribute('placeholder') || '').slice(0, 160),
    href: node.href || ''
  }));
  const scripts = Array.from(document.scripts)
    .map((script) => script.src)
    .filter(Boolean)
    .slice(0, 200);
  return { controls, scripts };
}
"""


def _clean_control(raw: dict[str, Any], *, authenticated: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("tag", "role", "type", "name"):
        value = str(raw.get(key) or "")[:120]
        if value:
            result[key] = safe_message(value)
    identifier = str(raw.get("id") or "")
    if identifier:
        result["id"] = "<id>" if len(identifier) > 80 else safe_message(identifier)
    for key in ("text", "aria_label", "title", "placeholder"):
        value = str(raw.get(key) or "")[:160]
        if value:
            result[key] = "<redacted-authenticated-text>" if authenticated else safe_message(value)
    href = str(raw.get("href") or "")
    if href:
        result["href"] = safe_url(href)
    return result


def collect_dom_inventory(store: str, mode: str, output: Path) -> dict[str, Any]:
    """Append safe home/product DOM observations to an existing capture JSON."""
    payload = json.loads(output.read_text(encoding="utf-8"))
    spec = STORES[store]
    product = payload.get("product")
    if not isinstance(product, dict) or not product.get("url"):
        try:
            product = choose_product(store)
        except Exception as exc:
            payload.setdefault("errors", []).append(
                {
                    "phase": "dom_product_discovery",
                    "type": type(exc).__name__,
                    "message": safe_message(str(exc)),
                }
            )
            product = None

    targets: list[tuple[str, str]] = [("home", spec.base_url)]
    if isinstance(product, dict) and product.get("url"):
        targets.append(("product", str(product["url"])))

    observations: list[dict[str, Any]] = []
    try:
        with sync_playwright() as playwright:
            headless = os.getenv("OPEN_GROCERY_CAPTURE_HEADLESS", "1").casefold() not in {
                "0",
                "false",
                "no",
                "off",
            }
            browser = playwright.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    locale="es-ES",
                    viewport={"width": 1440, "height": 1000},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.set_default_timeout(15_000)
                for label, url in targets:
                    try:
                        response = page.goto(url, wait_until="domcontentloaded")
                        click_words(
                            page,
                            ("aceptar todas", "aceptar cookies", "permitir todas", "accept all"),
                            ("button",),
                        )
                        page.wait_for_timeout(700)
                        raw = page.evaluate(_DOM_SCRIPT)
                        observations.append(
                            {
                                "page": label,
                                "status": response.status if response else None,
                                "url": safe_url(page.url),
                                "title": safe_message(page.title()),
                                "controls": [
                                    _clean_control(
                                        dict(item),
                                        authenticated=mode == "authenticated",
                                    )
                                    for item in raw.get("controls", [])
                                    if isinstance(item, dict)
                                ],
                                "script_sources": [
                                    safe_url(str(value))
                                    for value in raw.get("scripts", [])
                                    if value
                                ],
                            }
                        )
                    except Exception as exc:
                        observations.append(
                            {
                                "page": label,
                                "url": safe_url(url),
                                "error": safe_message(str(exc)),
                            }
                        )
            finally:
                browser.close()
    except Exception as exc:
        payload.setdefault("errors", []).append(
            {
                "phase": "dom_inventory",
                "type": type(exc).__name__,
                "message": safe_message(str(exc)),
            }
        )

    payload["dom_observations"] = observations
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload
