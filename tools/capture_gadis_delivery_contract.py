#!/usr/bin/env python3
"""Capture the Gadis address/slot/checkout-creation HTTP contract value-free.

Walks the authenticated flow one step deeper than capture_http_contract.py:
the saved-addresses page, opening the checkout, selecting a delivery schedule
when offered and triggering checkout creation exactly once.

Safety boundaries enforced by this probe:

- every non-GET request whose URL or body matches order/payment patterns is
  aborted before it leaves Chromium;
- payment, order-confirmation and purchase controls are never clicked;
- the walk stops after checkout creation and the test product is removed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from playwright.sync_api import Page, Request, Route, sync_playwright

from capture_http_contract import ContractProbe
from http_capture.common import (
    click_words,
    first_visible,
    safe_url,
)
from http_capture.dom import collect_dom_inventory
from http_capture.manifest import add_manifest

# Harder than the shared DANGEROUS filter: any non-GET touching an order,
# payment, Redsys or 3-D Secure route anywhere in the URL or body is aborted.
ORDER_BLOCK = re.compile(
    r"(?i)(/orders?(?:/|$)|payment|redsys|3ds|purchase|place\.?order|"
    r"submit\.?order|confirm\.?order)"
)
# Text of controls this probe must never activate.
FORBIDDEN_CLICK = re.compile(
    r"(?i)(pagar|pago\b|tarjeta|finalizar|hacer\s+pedido|realizar\s+pedido|"
    r"confirmar\s+pedido|contratar|suscribir)"
)
# Plain continuation controls that may create the checkout server-side.
CONTINUE_WORDS = ("continuar", "siguiente", "seguir", "aceptar y continuar")
SLOT_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
CHECKOUT_URL = re.compile(r"proceso-de-compra|checkout", re.I)
DAY_NAME = re.compile(
    r"(?i)\b(lunes|martes|mi[eé]rcoles|jueves|viernes|s[áa]bado|domingo)\b"
)


class GadisDeliveryProbe(ContractProbe):
    """Deep authenticated capture up to checkout creation only."""

    def route(self, route: Route, request: Request) -> None:
        body = request.post_data or ""
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
            ORDER_BLOCK.search(request.url) or ORDER_BLOCK.search(body)
        ):
            self.blocked.append(
                {
                    "phase": self.phase,
                    "method": request.method,
                    "url": safe_url(request.url),
                    "reason": "delivery-probe hard stop (order/payment)",
                }
            )
            route.abort("blockedbyclient")
            return
        super().route(route, request)

    @staticmethod
    def _visible_text(element: any) -> str:  # noqa: ANN401
        try:
            if not element.is_visible():
                return ""
            return (element.inner_text() or "").strip()
        except Exception:
            return ""

    def addresses_page(self, page: Page) -> None:
        page.goto(
            self.spec.base_url.rstrip("/") + "/addresses",
            wait_until="domcontentloaded",
        )
        self.accept_cookies(page)
        self.dismiss_dialogs(page)
        page.wait_for_timeout(2500)

    def open_checkout(self, page: Page) -> None:
        self.goto_cart(page)
        # Only the checkout entry point; never "hacer pedido"/"ir al pago".
        if not click_words(page, ("tramitar pedido",)):
            target = first_visible(
                page.locator(
                    "a[href*='checkout' i],a[href*='proceso-de-compra' i]"
                )
            )
            if target is None:
                raise RuntimeError("checkout entry control not found")
            target.click()
        # The storefront may bootstrap a silent NextAuth/Keycloak round-trip
        # before rendering the checkout; wait for the process page itself.
        try:
            page.wait_for_url(CHECKOUT_URL, timeout=30000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        self.dismiss_dialogs(page)
        page.wait_for_timeout(3000)

    def select_schedule(self, page: Page) -> None:
        """Pick the first visible delivery time range, if any is offered."""
        # A date tab (weekday name) may need selecting before time ranges
        # appear; this only changes the displayed day, never the order.
        for selector in ("[role=tab]", "button", "div[role=button]"):
            locator = page.locator(selector)
            for index in range(min(locator.count(), 60)):
                element = locator.nth(index)
                text = self._visible_text(element)
                low = text.casefold()
                if not text or FORBIDDEN_CLICK.search(low):
                    continue
                if DAY_NAME.search(text) and len(text) <= 40:
                    try:
                        element.click()
                    except Exception:
                        continue
                    page.wait_for_timeout(1500)
                    break
            else:
                continue
            break
        selectors = (
            "[role=radio]",
            "[role=option]",
            "li[role=button]",
            "div[role=button]",
            "button",
            "label",
        )
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 120)):
                element = locator.nth(index)
                text = self._visible_text(element)
                low = text.casefold()
                if not text or FORBIDDEN_CLICK.search(low):
                    continue
                if SLOT_TIME.search(text):
                    element.click()
                    page.wait_for_timeout(2500)
                    return
        raise RuntimeError("no selectable delivery slot was offered")

    def _click_continue_once(self, page: Page) -> bool:
        locator = page.locator("button, [role=button]")
        for index in range(min(locator.count(), 80)):
            element = locator.nth(index)
            text = self._visible_text(element)
            low = text.casefold()
            if not low or FORBIDDEN_CLICK.search(low):
                continue
            if any(word in low for word in CONTINUE_WORDS):
                element.click()
                page.wait_for_timeout(3000)
                return True
        return False

    def create_checkout(self, page: Page) -> None:
        """Trigger checkout creation once; never approach payment."""
        if not self._click_continue_once(page):
            raise RuntimeError("no safe continue control found")
        # Intentionally no further interaction after this point.

    def _run_deep(self) -> int:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._run_action("product_discovery", self.discover_product)
            with sync_playwright() as playwright:
                headless = os.getenv(
                    "OPEN_GROCERY_CAPTURE_HEADLESS", "1"
                ).casefold() not in {"0", "false", "no", "off"}
                browser = playwright.chromium.launch(headless=headless)
                try:
                    state_path = self._state_path()
                    if not state_path.exists():
                        raise RuntimeError(
                            f"no saved Gadis session at {state_path}; "
                            "run login_with_browser first"
                        )
                    context = browser.new_context(
                        locale="es-ES",
                        viewport={"width": 1440, "height": 1000},
                        storage_state=str(state_path),
                    )
                    context.route("**/*", self.route)
                    page = context.new_page()
                    page.set_default_timeout(15000)
                    page.on("request", self.on_request)
                    page.on("response", self.on_response)

                    self._run_action(
                        "bootstrap",
                        lambda: page.goto(
                            self.spec.base_url, wait_until="domcontentloaded"
                        ),
                    )
                    self._run_action("login", lambda: self.login(page))
                    self._run_action("cart_initial", lambda: self.goto_cart(page))
                    self._run_action("add", lambda: self.add(page))
                    self._run_action(
                        "cart_after_add", lambda: self.goto_cart(page)
                    )
                    self._run_action(
                        "addresses_page", lambda: self.addresses_page(page)
                    )
                    self._run_action(
                        "checkout_open", lambda: self.open_checkout(page)
                    )
                    self._run_action(
                        "schedule_select", lambda: self.select_schedule(page)
                    )
                    self._run_action(
                        "checkout_create", lambda: self.create_checkout(page)
                    )
                    self._run_action("cleanup", lambda: self.cleanup(page))
                finally:
                    browser.close()
        except Exception as exc:
            self.record_error(self.phase or "browser", exc)
        finally:
            if not self.events:
                self.errors.append(
                    {
                        "phase": self.phase or "capture",
                        "type": "EmptyCapture",
                        "message": (
                            "no HTTP traffic was captured; the storefront may "
                            "be unreachable or blocked by anti-bot"
                        ),
                    }
                )
            self._write_report()
        return 0 if self.events else 1

    def run(self) -> int:
        status = self._run_deep()
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        payload["bundle_candidates"] = self.bundle_candidates
        self.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        collect_dom_inventory(self.spec.key, self.mode, self.output)
        add_manifest(self.output)
        return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Deep value-free Gadis delivery/checkout-creation capture. "
            "Order and payment requests are blocked before leaving the browser."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return GadisDeliveryProbe("gadis", "authenticated", args.output).run()


if __name__ == "__main__":
    raise SystemExit(main())
