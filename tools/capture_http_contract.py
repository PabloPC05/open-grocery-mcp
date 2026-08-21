#!/usr/bin/env python3
"""Capture sanitized Gadis/Froiz HTTP contracts without submitting an order."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import Locator, Page, Request, Route, sync_playwright

from http_capture_sanitize import sanitize_har
from open_grocery_mcp.providers.browser_config import FROIZ_BROWSER_CONFIG, GADIS_BROWSER_CONFIG
from open_grocery_mcp.providers.froiz import FroizProvider
from open_grocery_mcp.providers.gadis import GadisProvider

DANGEROUS_URL = re.compile(
    r"(?i)(/checkouts?/.*/orders?/?$|/orders?/?$|place.?order|submit.?order|"
    r"confirm.?order|complete.?checkout|payment|payments|redsys|3ds|purchase)"
)
DANGEROUS_BODY = re.compile(r"(?i)(place.?order|submit.?order|confirm.?order|card.?number|cvv|cvc)")
RESTRICTED = re.compile(r"(?i)\b(vino|cerveza|whisk(?:y|ey)|vodka|ginebra|ron|licor|cava|sidra|tabaco|cigarr|vape|nicotina)\b")

CONFIGS = {"gadis": GADIS_BROWSER_CONFIG, "froiz": FROIZ_BROWSER_CONFIG}
SECRETS = {
    "gadis": ("GADIS_TEST_USERNAME", "GADIS_TEST_PASSWORD"),
    "froiz": ("FROIZ_TEST_USERNAME", "FROIZ_TEST_PASSWORD"),
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def first_visible(locator: Locator) -> Locator | None:
    try:
        for index in range(min(locator.count(), 20)):
            candidate = locator.nth(index)
            if candidate.is_visible():
                return candidate
    except Exception:
        return None
    return None


def click(page: Page, patterns: Iterable[str], roles: tuple[str, ...] = ("button", "link")) -> bool:
    expression = re.compile("(?:" + "|".join(patterns) + ")", re.I)
    for role in roles:
        try:
            target = first_visible(page.get_by_role(role, name=expression))
            if target:
                target.click()
                return True
        except Exception:
            pass
    try:
        target = first_visible(page.locator("button,a,[role='button']").filter(has_text=expression))
        if target:
            target.click()
            return True
    except Exception:
        pass
    return False


def choose_product(store: str) -> dict[str, Any]:
    provider = GadisProvider() if store == "gadis" else FroizProvider()
    try:
        for query in ("leche entera 1 l", "arroz 1 kg", "agua mineral"):
            for product in provider.search(query, limit=10):
                if product.url and product.price > 0 and not RESTRICTED.search(product.name):
                    return {"id": product.id, "name": product.name, "url": product.url, "price": float(product.price)}
    finally:
        provider.close()
    raise RuntimeError(f"no safe diagnostic product found for {store}")


def login(page: Page, store: str, config: Any) -> None:
    username_name, password_name = SECRETS[store]
    username, password = os.getenv(username_name, ""), os.getenv(password_name, "")
    if not username or not password:
        raise RuntimeError(f"authenticated mode requires {username_name} and {password_name}")
    if not click(page, (r"iniciar sesión", r"acceder", r"mi cuenta", r"identificarse")):
        target = first_visible(page.locator("a[href*='login' i],a[href*='account' i],a[href*='cuenta' i]"))
        if target:
            target.click()
    page.wait_for_timeout(900)
    user = first_visible(page.locator("input[type='email'],input[name*='email' i],input[name*='user' i],input[autocomplete='username']"))
    secret = first_visible(page.locator("input[type='password'],input[autocomplete='current-password']"))
    if not user or not secret:
        raise RuntimeError("could not locate login form")
    user.fill(username)
    secret.fill(password)
    submit = first_visible(page.locator("button[type='submit'],input[type='submit']"))
    if submit:
        submit.click()
    else:
        secret.press("Enter")
    page.wait_for_timeout(1800)


def goto_cart(page: Page, config: Any) -> None:
    page.goto(config.base_url, wait_until="domcontentloaded")
    click(page, (r"aceptar todas", r"aceptar cookies", r"accept all"), ("button",))
    if click(page, config.cart_patterns):
        page.wait_for_timeout(700)
        return
    for path in config.cart_paths:
        try:
            response = page.goto(config.base_url.rstrip("/") + path, wait_until="domcontentloaded")
            if response is None or response.status < 400:
                return
        except Exception:
            pass
    raise RuntimeError("could not navigate to cart")


def product_row(page: Page, product: dict[str, Any]) -> Locator | None:
    target = first_visible(page.get_by_text(product["name"], exact=False))
    if not target:
        return None
    row = target.locator(
        "xpath=ancestor::li[1] | ancestor::article[1] | ancestor::tr[1] | "
        "ancestor::*[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'cart-item')][1] | "
        "ancestor::*[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'basket-item')][1]"
    )
    return row.first if row.count() else target.locator("xpath=..")


def add_product(page: Page, config: Any, product: dict[str, Any]) -> None:
    page.goto(product["url"], wait_until="domcontentloaded")
    click(page, (r"aceptar todas", r"aceptar cookies", r"accept all"), ("button",))
    if click(page, config.add_patterns, ("button",)):
        page.wait_for_timeout(900)
        return
    target = first_visible(page.locator("button[aria-label*='añadir' i],button[title*='añadir' i],button[data-testid*='add' i]"))
    if not target:
        raise RuntimeError("could not find add-to-cart control")
    target.click()
    page.wait_for_timeout(900)


def set_quantity(page: Page, product: dict[str, Any], value: int) -> None:
    row = product_row(page, product)
    if not row:
        raise RuntimeError("could not locate diagnostic product in cart")
    field = first_visible(row.locator("input[type='number'],input[name*='quantity' i],input[name*='cantidad' i]"))
    if field:
        field.fill(str(value))
        field.press("Enter")
        page.wait_for_timeout(800)
        return
    selector = "button[aria-label*='aumentar' i],button[title*='aumentar' i]" if value == 2 else "button[aria-label*='disminuir' i],button[title*='disminuir' i]"
    target = first_visible(row.locator(selector))
    if not target:
        raise RuntimeError("could not locate safe quantity control")
    target.click()
    page.wait_for_timeout(800)


def cleanup(page: Page, config: Any, product: dict[str, Any]) -> None:
    goto_cart(page, config)
    row = product_row(page, product)
    if not row:
        return
    target = first_visible(row.locator("button[aria-label*='eliminar' i],button[aria-label*='quitar' i],button[title*='eliminar' i]"))
    if target:
        target.click()
        page.wait_for_timeout(700)
        return
    expression = re.compile("(?:" + "|".join(config.remove_patterns) + ")", re.I)
    target = first_visible(row.locator("button,a,[role='button']").filter(has_text=expression))
    if target:
        target.click()
        page.wait_for_timeout(700)


def run(store: str, mode: str, output: Path) -> int:
    config = CONFIGS[store]
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_har = output.with_suffix(".raw.har")
    product = choose_product(store)
    errors: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []

    def guard(route: Route, request: Request) -> None:
        body = request.post_data or ""
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (DANGEROUS_URL.search(request.url) or DANGEROUS_BODY.search(body)):
            blocked.append({"method": request.method, "url": request.url.split("?", 1)[0]})
            route.abort("blockedbyclient")
        else:
            route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=os.getenv("OPEN_GROCERY_CAPTURE_HEADLESS", "1") not in {"0", "false"})
        try:
            context = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}, record_har_path=str(raw_har), record_har_content="embed", record_har_mode="full")
            context.route("**/*", guard)
            page = context.new_page()
            page.set_default_timeout(15_000)
            actions = [
                ("bootstrap", lambda: page.goto(config.base_url, wait_until="domcontentloaded")),
                ("login", lambda: login(page, store, config) if mode == "authenticated" else None),
                ("add", lambda: add_product(page, config, product)),
                ("cart", lambda: goto_cart(page, config)),
                ("quantity_2", lambda: set_quantity(page, product, 2)),
                ("quantity_1", lambda: set_quantity(page, product, 1)),
                ("checkout", lambda: (goto_cart(page, config), click(page, config.checkout_patterns), page.wait_for_timeout(1200))),
                ("cleanup", lambda: cleanup(page, config, product)),
            ]
            for phase, action in actions:
                try:
                    action()
                except Exception as exc:
                    errors.append({"phase": phase, "type": type(exc).__name__, "message": str(exc)[:800]})
            context.close()
        finally:
            browser.close()

    metadata = {"captured_at": now(), "store": store, "mode": mode, "product": product, "blocked": blocked, "errors": errors}
    payload = sanitize_har(raw_har, output, metadata)
    raw_har.unlink(missing_ok=True)
    return 0 if payload["entries"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True, choices=sorted(CONFIGS))
    parser.add_argument("--mode", choices=("guest", "authenticated"), default="guest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return run(args.store, args.mode, args.output)


if __name__ == "__main__":
    sys.exit(main())
