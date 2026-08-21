"""Playwright probe that records sanitized request/response contracts."""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from playwright.sync_api import Page, Request, Response, Route, sync_playwright

from .common import (
    DANGEROUS,
    RELEVANT,
    STORES,
    choose_product,
    click_words,
    first_visible,
    safe_headers,
    safe_url,
    shape,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


class Probe:
    def __init__(self, store: str, mode: str, output: Path) -> None:
        self.spec = STORES[store]
        self.mode = mode
        self.output = output
        self.phase = "init"
        self.events: list[dict[str, Any]] = []
        self.blocked: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []
        self.skipped: list[dict[str, str]] = []
        # Product discovery performs live catalogue I/O. It must happen inside
        # run(), after diagnostics have been initialized, so a catalogue outage
        # cannot prevent a useful capture report from being written.
        self.product: dict[str, Any] | None = None

    def record_error(self, phase: str, exc: BaseException) -> None:
        self.errors.append(
            {
                "phase": phase,
                "type": type(exc).__name__,
                "message": str(exc)[:800],
            }
        )

    def skip(self, phase: str, reason: str) -> None:
        self.skipped.append({"phase": phase, "reason": reason})

    def relevant(self, request: Request) -> bool:
        host = (urlsplit(request.url).hostname or "").casefold()
        if any(
            item in host
            for item in (
                "google-analytics",
                "googletagmanager",
                "doubleclick",
                "facebook",
                "hotjar",
                "sentry",
            )
        ):
            return False
        if request.resource_type in {"image", "font", "media", "stylesheet"}:
            return False
        return (
            request.method != "GET"
            or request.resource_type in {"xhr", "fetch"}
            or bool(RELEVANT.search(request.url))
        )

    def route(self, route: Route, request: Request) -> None:
        body = request.post_data or ""
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
            DANGEROUS.search(request.url) or DANGEROUS.search(body)
        ):
            self.blocked.append(
                {
                    "phase": self.phase,
                    "method": request.method,
                    "url": safe_url(request.url),
                    "reason": "potential order/payment request",
                }
            )
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def on_request(self, request: Request) -> None:
        if not self.relevant(request):
            return
        body: Any = None
        if request.post_data:
            try:
                body = shape(json.loads(request.post_data))
            except Exception:
                body = "<non-json-body>"
        self.events.append(
            {
                "kind": "request",
                "phase": self.phase,
                "at": now(),
                "method": request.method,
                "url": safe_url(request.url),
                "resource_type": request.resource_type,
                "headers": safe_headers(request.headers),
                "body": body,
            }
        )

    def on_response(self, response: Response) -> None:
        if not self.relevant(response.request):
            return
        body: Any = None
        if "json" in response.headers.get("content-type", "").casefold():
            try:
                body = shape(response.json())
            except Exception:
                pass
        self.events.append(
            {
                "kind": "response",
                "phase": self.phase,
                "at": now(),
                "method": response.request.method,
                "url": safe_url(response.url),
                "status": response.status,
                "headers": safe_headers(response.headers),
                "body": body,
            }
        )

    @staticmethod
    def accept_cookies(page: Page) -> None:
        click_words(
            page,
            ("aceptar todas", "aceptar cookies", "permitir todas", "accept all"),
            ("button",),
        )

    def discover_product(self) -> None:
        self.product = choose_product(self.spec.key)

    def login(self, page: Page) -> None:
        if self.mode != "authenticated":
            return
        username = os.getenv(self.spec.username_env, "")
        password = os.getenv(self.spec.password_env, "")
        if not username or not password:
            raise RuntimeError(
                f"missing {self.spec.username_env}/{self.spec.password_env}"
            )
        if not click_words(page, self.spec.login_words):
            target = first_visible(
                page.locator(
                    "a[href*='login' i],a[href*='account' i],a[href*='cuenta' i]"
                )
            )
            if target:
                target.click()
        page.wait_for_timeout(700)
        user = first_visible(
            page.locator(
                "input[type=email],input[name*='email' i],input[name*='user' i],"
                "input[autocomplete=username]"
            )
        )
        password_input = first_visible(
            page.locator(
                "input[type=password],input[autocomplete=current-password]"
            )
        )
        if user is None or password_input is None:
            raise RuntimeError("login fields not found")
        user.fill(username)
        password_input.fill(password)
        submit = first_visible(
            page.locator("button[type=submit],input[type=submit]")
        )
        submit.click() if submit else password_input.press("Enter")
        page.wait_for_timeout(1600)

    def _require_product(self) -> dict[str, Any]:
        if self.product is None:
            raise RuntimeError("diagnostic product is unavailable")
        return self.product

    def add(self, page: Page) -> None:
        product = self._require_product()
        page.goto(product["url"], wait_until="domcontentloaded")
        self.accept_cookies(page)
        if click_words(page, self.spec.add_words, ("button",)):
            page.wait_for_timeout(700)
            return
        target = first_visible(
            page.locator(
                "button[aria-label*='añadir' i],button[title*='añadir' i],"
                "button[data-testid*='add' i]"
            )
        )
        if target is None:
            raise RuntimeError("add button not found")
        target.click()
        page.wait_for_timeout(700)

    def goto_cart(self, page: Page) -> None:
        page.goto(self.spec.base_url, wait_until="domcontentloaded")
        self.accept_cookies(page)
        if click_words(page, self.spec.cart_words):
            page.wait_for_timeout(700)
            return
        for path in self.spec.cart_paths:
            try:
                response = page.goto(
                    self.spec.base_url.rstrip("/") + path,
                    wait_until="domcontentloaded",
                )
                if response is None or response.status < 400:
                    return
            except Exception:
                pass
        raise RuntimeError("cart navigation failed")

    def row(self, page: Page) -> Any | None:
        product = self._require_product()
        target = first_visible(page.get_by_text(product["name"], exact=False))
        if target is None:
            return None
        row = target.locator(
            "xpath=ancestor::li[1] | ancestor::article[1] | ancestor::tr[1] | "
            "ancestor::*[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz'),'cart-item')][1] | "
            "ancestor::*[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz'),'basket-item')][1]"
        )
        return row.first if row.count() else target.locator("xpath=..")

    def quantity(self, page: Page, value: int) -> None:
        row = self.row(page)
        if row is None:
            raise RuntimeError("cart row not found")
        field = first_visible(
            row.locator(
                "input[type=number],input[name*='quantity' i],"
                "input[name*='cantidad' i]"
            )
        )
        if field:
            field.fill(str(value))
            field.press("Enter")
            page.wait_for_timeout(700)
            return
        selector = (
            "button[aria-label*='aumentar' i],button[title*='aumentar' i]"
            if value == 2
            else "button[aria-label*='disminuir' i],button[title*='disminuir' i]"
        )
        target = first_visible(row.locator(selector))
        if target is None:
            raise RuntimeError("quantity control not found")
        target.click()
        page.wait_for_timeout(700)

    def checkout(self, page: Page) -> None:
        self.goto_cart(page)
        if not click_words(page, self.spec.checkout_words):
            target = first_visible(
                page.locator(
                    "a[href*='checkout' i],button[data-testid*='checkout' i]"
                )
            )
            if target is None:
                raise RuntimeError("checkout control not found")
            target.click()
        page.wait_for_timeout(1200)

    def cleanup(self, page: Page) -> None:
        self.goto_cart(page)
        row = self.row(page)
        if row is None:
            return
        pattern = re.compile(
            "(?:" + "|".join(re.escape(item) for item in self.spec.remove_words) + ")",
            re.I,
        )
        target = first_visible(
            row.locator("button,a,[role=button]").filter(has_text=pattern)
        )
        if target:
            target.click()
            page.wait_for_timeout(600)

    def _write_report(self) -> None:
        self.output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "store": self.spec.key,
                    "mode": self.mode,
                    "captured_at": now(),
                    "product": self.product,
                    "events": self.events,
                    "blocked": self.blocked,
                    "errors": self.errors,
                    "skipped": self.skipped,
                    "safety": {
                        "order_clicked": False,
                        "credentials_recorded": False,
                        "values_sanitized": True,
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def _run_action(
        self,
        phase: str,
        action: Callable[[], Any],
    ) -> None:
        self.phase = phase
        try:
            action()
        except Exception as exc:
            self.record_error(phase, exc)

    def run(self) -> int:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._run_action("product_discovery", self.discover_product)
            with sync_playwright() as playwright:
                headless = os.getenv(
                    "OPEN_GROCERY_CAPTURE_HEADLESS", "1"
                ).casefold() not in {"0", "false", "no", "off"}
                browser = playwright.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(
                        locale="es-ES",
                        viewport={"width": 1440, "height": 1000},
                    )
                    context.route("**/*", self.route)
                    page = context.new_page()
                    page.set_default_timeout(15000)
                    page.on("request", self.on_request)
                    page.on("response", self.on_response)

                    self._run_action(
                        "bootstrap",
                        lambda: page.goto(
                            self.spec.base_url,
                            wait_until="domcontentloaded",
                        ),
                    )
                    self._run_action("login", lambda: self.login(page))
                    self._run_action("cart_initial", lambda: self.goto_cart(page))

                    if self.product is None:
                        for phase in (
                            "add",
                            "cart_after_add",
                            "quantity_2",
                            "quantity_1",
                            "checkout",
                            "cleanup",
                        ):
                            self.skip(
                                phase,
                                "diagnostic product discovery failed; bootstrap and cart traffic were still captured",
                            )
                    else:
                        self._run_action("add", lambda: self.add(page))
                        self._run_action(
                            "cart_after_add",
                            lambda: self.goto_cart(page),
                        )
                        self._run_action(
                            "quantity_2",
                            lambda: self.quantity(page, 2),
                        )
                        self._run_action(
                            "quantity_1",
                            lambda: self.quantity(page, 1),
                        )
                        self._run_action("checkout", lambda: self.checkout(page))
                        self._run_action("cleanup", lambda: self.cleanup(page))
                finally:
                    browser.close()
        except Exception as exc:
            # Browser installation/launch failures must also produce a sanitized
            # report, instead of leaving only an opaque exit code in Actions.
            self.record_error(self.phase or "browser", exc)
        finally:
            self._write_report()
        return 0 if self.events else 1
