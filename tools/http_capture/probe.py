"""Playwright probe that records sanitized request/response contracts."""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Page, Request, Response, Route, sync_playwright

from .common import (
    DANGEROUS,
    RELEVANT,
    STORES,
    choose_product,
    click_words,
    first_visible,
    safe_headers,
    safe_message,
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
        self.original_quantity: int | None = None
        self.last_verified_quantity: int | None = None
        self.restoration_verified = False
        # Product discovery performs live catalogue I/O. It must happen inside
        # run(), after diagnostics have been initialized, so a catalogue outage
        # cannot prevent a useful capture report from being written.
        self.product: dict[str, Any] | None = None
        self._warehouse: str | None = None

    def record_error(self, phase: str, exc: BaseException) -> None:
        self.errors.append(
            {
                "phase": phase,
                "type": type(exc).__name__,
                "message": safe_message(str(exc)),
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
        # A document/script navigation is always recorded so a bootstrap that
        # never fires an xhr/fetch request (cookie walls, redirects, a static
        # storefront) cannot produce an empty capture.
        return (
            request.method != "GET"
            or request.resource_type in {"xhr", "fetch", "document"}
            or bool(RELEVANT.search(request.url))
        )

    def route(self, route: Route, request: Request) -> None:
        body = request.post_data or ""
        dangerous = DANGEROUS.search(request.url) or DANGEROUS.search(body)
        order_probe_write = self.phase == "order_submit_probe" and request.method not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }
        if dangerous or order_probe_write:
            self.blocked.append(
                {
                    "phase": self.phase,
                    "method": request.method,
                    "url": safe_url(request.url),
                    "reason": (
                        "all writes are blocked during order_submit_probe"
                        if order_probe_write and not dangerous
                        else "potential order/payment request"
                    ),
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

    @staticmethod
    def dismiss_dialogs(page: Page) -> None:
        """Close retailer session/schedule prompts without changing the cart.

        Gadis shows an "AMPLIAR TIEMPO"/"Cancelar" refresh-schedule dialog while
        a delivery slot is pending; leaving it up blocks the add-to-cart and
        quantity controls underneath. Extending the schedule is harmless.
        """

        for label in ("AMPLIAR TIEMPO", "ampliar tiempo", "Ampliar tiempo"):
            try:
                target = first_visible(page.get_by_role("button", name=label))
                if target is not None:
                    target.click()
                    page.wait_for_timeout(800)
                    return
            except Exception:
                pass
        try:
            target = page.get_by_test_id(
                "buttonComponentAcceptRefreshScheduleButton"
            )
            if target.is_visible():
                target.click()
                page.wait_for_timeout(800)
        except Exception:
            pass

    def discover_product(self) -> None:
        self.product = choose_product(self.spec.key)
        if self.spec.key == "mercadona" and self.product:
            warehouse = str(self.product.pop("_warehouse", "")).strip()
            self._warehouse = warehouse or None

    def _state_path(self) -> Path:
        configured = os.getenv(f"OPEN_GROCERY_{self.spec.key.upper()}_STATE_PATH")
        if configured:
            return Path(configured).expanduser()
        root = Path(
            os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")
        ).expanduser()
        return root / self.spec.key / "storage_state.json"

    def _setup_page(self, page: Page) -> None:
        """Attach listeners before a page's first navigation.

        Mercadona may open a new tab for account/session flows.  Registering
        the context-level listener before bootstrap keeps those requests in
        the same sanitized report instead of silently losing them.
        """
        page.set_default_timeout(15000)
        page.on("request", self.on_request)
        page.on("response", self.on_response)

    def login(self, page: Page) -> None:
        if self.mode != "authenticated":
            return
        username = os.getenv(self.spec.username_env, "")
        password = os.getenv(self.spec.password_env, "")
        if not username or not password:
            if self._state_path().exists():
                return
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
        self.dismiss_dialogs(page)
        for word in self.spec.add_words:
            target = first_visible(
                page.get_by_role(
                    "button",
                    name=re.compile(rf"^{re.escape(word)}$", re.IGNORECASE),
                )
            )
            if target is not None:
                target.click()
                page.wait_for_timeout(700)
                return
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
        self.dismiss_dialogs(page)
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
                    self.dismiss_dialogs(page)
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
            "'abcdefghijklmnopqrstuvwxyz'),'basket-item')][1] | "
            "ancestor::*[.//input[contains(@name,'quantity') or "
            "contains(@name,'cantidad')]][1]"
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

    def product_quantity(self, page: Page) -> int:
        """Read the probe product quantity without changing retailer state."""
        row = self.row(page)
        if row is None:
            return 0
        field = first_visible(
            row.locator(
                "input[type=number],input[name*='quantity' i],"
                "input[name*='cantidad' i]"
            )
        )
        if field is None:
            raise RuntimeError(
                "existing probe product has no unambiguous quantity control"
            )
        try:
            quantity = Decimal(str(field.input_value()).replace(",", ".").strip())
        except (InvalidOperation, ValueError, AttributeError):
            raise RuntimeError("probe product quantity could not be read safely") from None
        if (
            not quantity.is_finite()
            or quantity < 0
            or quantity > 1000
            or quantity != quantity.to_integral_value()
        ):
            raise RuntimeError("probe product quantity is outside safe whole units")
        return int(quantity)

    def snapshot_cart(self, page: Page) -> None:
        self.goto_cart(page)
        self.original_quantity = self.product_quantity(page)
        self.last_verified_quantity = self.original_quantity

    def _expect_quantity(self, page: Page, expected: int) -> None:
        observed = self.product_quantity(page)
        if observed != expected:
            raise RuntimeError(
                f"probe product quantity is {observed}, expected {expected}"
            )
        self.last_verified_quantity = observed

    def _read_after_add(self, page: Page) -> None:
        if self.original_quantity is None:
            raise RuntimeError("cart add was not preceded by a safe snapshot")
        self.goto_cart(page)
        self._expect_quantity(page, self.original_quantity + 1)

    def _set_and_verify_quantity(self, page: Page, value: int) -> None:
        self.quantity(page, value)
        self.goto_cart(page)
        self._expect_quantity(page, value)

    def checkout(self, page: Page) -> None:
        self.goto_cart(page)
        # Only follow a navigational link. Labels such as "tramitar pedido"
        # are not safe enough for a generic probe because some storefronts
        # bind them directly to the order-creation request.
        target = first_visible(
            page.locator(
                "a[href*='checkout' i],a[href*='proceso-de-compra' i]"
            )
        )
        if target is None:
            raise RuntimeError("safe checkout navigation link not found")
        href = str(target.get_attribute("href") or "").strip()
        destination = urljoin(page.url or self.spec.base_url, href)
        destination_parts = urlsplit(destination)
        base_parts = urlsplit(self.spec.base_url)
        if (
            not href
            or destination_parts.scheme not in {"http", "https"}
            or destination_parts.hostname != base_parts.hostname
            or DANGEROUS.search(destination)
        ):
            raise RuntimeError("checkout link did not resolve to a safe retailer URL")
        page.goto(destination, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

    def cleanup(self, page: Page) -> None:
        self.goto_cart(page)
        if self.original_quantity is None or self.last_verified_quantity is None:
            raise RuntimeError("cart mutation was not preceded by a safe snapshot")
        current_quantity = self.product_quantity(page)
        if current_quantity != self.last_verified_quantity:
            raise RuntimeError(
                "probe product changed since the last verified read; automatic "
                "restoration was refused"
            )
        if current_quantity == self.original_quantity:
            self.restoration_verified = True
            return
        if self.original_quantity > 0:
            self.quantity(page, self.original_quantity)
            self.goto_cart(page)
            if self.product_quantity(page) != self.original_quantity:
                raise RuntimeError("original cart quantity was not restored")
            self.restoration_verified = True
            return
        row = self.row(page)
        if row is None:
            self.restoration_verified = True
            return
        pattern = re.compile(
            "(?:" + "|".join(re.escape(item) for item in self.spec.remove_words) + ")",
            re.I,
        )
        target = first_visible(
            row.locator("button,a,[role=button]").filter(has_text=pattern)
        )
        if target is None:
            target = first_visible(
                row.locator(
                    "button[data-testid*='remove' i],"
                    "button[aria-label*='eliminar' i],"
                    "button[title*='eliminar' i],"
                    "button[data-testid*='eliminar' i]"
                )
            )
        if target:
            target.click()
            page.wait_for_timeout(600)
        self.goto_cart(page)
        if self.product_quantity(page) != 0:
            raise RuntimeError("probe product removal was not verified")
        self.restoration_verified = True

    def _write_report(self) -> None:
        product = None
        if self.product is not None:
            product = {
                **self.product,
                "url": safe_url(str(self.product.get("url") or "")),
            }
        self.output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "store": self.spec.key,
                    "mode": self.mode,
                    "captured_at": now(),
                    "product": product,
                    "events": self.events,
                    "blocked": self.blocked,
                    "errors": self.errors,
                    "skipped": self.skipped,
                    "safety": {
                        "order_clicked": False,
                        "credentials_recorded": False,
                        "values_sanitized": True,
                        "original_cart_restored": self.restoration_verified,
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
    ) -> bool:
        self.phase = phase
        try:
            action()
        except Exception as exc:
            self.record_error(phase, exc)
            return False
        return True

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
                    context_args: dict[str, Any] = {
                        "locale": "es-ES",
                        "viewport": {"width": 1440, "height": 1000},
                    }
                    if self.spec.key == "mercadona" and self._warehouse:
                        context_args["extra_http_headers"] = {
                            "x-customer-wh": self._warehouse,
                        }
                    if self.mode == "authenticated":
                        state_path = self._state_path()
                        if state_path.exists():
                            context_args["storage_state"] = str(state_path)
                    context = browser.new_context(**context_args)
                    context.route("**/*", self.route)
                    page = context.new_page()
                    self._setup_page(page)
                    context.on("page", self._setup_page)

                    self._run_action(
                        "bootstrap",
                        lambda: page.goto(
                            self.spec.base_url,
                            wait_until="domcontentloaded",
                        ),
                    )
                    self._run_action("login", lambda: self.login(page))
                    self._run_action("cart_initial", lambda: self.snapshot_cart(page))

                    if self.product is None or self.original_quantity is None:
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
                                "diagnostic product discovery or safe cart snapshot failed; "
                                "bootstrap and cart traffic were still captured",
                            )
                    else:
                        add_ok = self._run_action("add", lambda: self.add(page))
                        add_verified = add_ok and self._run_action(
                            "cart_after_add",
                            lambda: self._read_after_add(page),
                        )
                        if add_verified:
                            second_quantity = self.original_quantity + 2
                            first_quantity = self.original_quantity + 1
                            quantity_2_ok = self._run_action(
                                "quantity_2",
                                lambda: self._set_and_verify_quantity(
                                    page, second_quantity
                                ),
                            )
                            if quantity_2_ok:
                                quantity_1_ok = self._run_action(
                                    "quantity_1",
                                    lambda: self._set_and_verify_quantity(
                                        page, first_quantity
                                    ),
                                )
                            else:
                                quantity_1_ok = False
                                self.skip(
                                    "quantity_1",
                                    "the preceding quantity mutation was not verified",
                                )
                            if quantity_1_ok:
                                self._run_action(
                                    "checkout", lambda: self.checkout(page)
                                )
                            else:
                                self.skip(
                                    "checkout",
                                    "cart mutations were not fully verified",
                                )
                        else:
                            for phase in ("quantity_2", "quantity_1", "checkout"):
                                self.skip(
                                    phase,
                                    "the add mutation was not verified",
                                )
                        self._run_action("cleanup", lambda: self.cleanup(page))
                finally:
                    browser.close()
        except Exception as exc:
            # Browser installation/launch failures must also produce a sanitized
            # report, instead of leaving only an opaque exit code in Actions.
            self.record_error(self.phase or "browser", exc)
        finally:
            if not self.events:
                self.errors.append(
                    {
                        "phase": self.phase or "capture",
                        "type": "EmptyCapture",
                        "message": (
                            "no HTTP traffic was captured; the storefront may be "
                            "unreachable, blocked by anti-bot, or not issuing API calls"
                        ),
                    }
                )
            self._write_report()
        return 0 if self.events else 1
