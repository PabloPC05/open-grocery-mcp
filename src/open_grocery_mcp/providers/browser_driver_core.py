"""Browser lifecycle, login and safe retailer navigation."""

from __future__ import annotations

import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urljoin

from open_grocery_mcp.errors import AuthenticationRequired, InvalidRequest, ProviderError
from open_grocery_mcp.providers.browser_config import BrowserStoreConfig
from open_grocery_mcp.providers.browser_normalize import normalized_text

_CAPTURE_RE = re.compile(
    r"cart|basket|cesta|carrito|checkout|address|direccion|delivery|slot|order|pedido",
    re.I,
)


class BrowserDriverCore:
    def __init__(
        self,
        config: BrowserStoreConfig,
        *,
        state_path: Path,
        checkout_store: Path,
        timeout_seconds: int = 30,
    ) -> None:
        self.config = config
        self.state_path = state_path
        self.checkout_store = checkout_store
        self.timeout_ms = max(5, timeout_seconds) * 1000
        self._mutex = threading.RLock()

    @staticmethod
    def _playwright():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised by installation, not unit tests.
            raise AuthenticationRequired(
                'browser workflows require `pip install "open-grocery-mcp[browser]"` '
                'and `playwright install chromium`'
            ) from exc
        return sync_playwright

    def _headless(self) -> bool:
        value = os.getenv("OPEN_GROCERY_BROWSER_HEADLESS", "1").casefold()
        return value not in {"0", "false", "no", "off"}

    def _launch_kwargs(self, *, headless: bool) -> dict[str, Any]:
        prefix = f"OPEN_GROCERY_{self.config.key.upper()}_"
        executable = os.getenv(prefix + "BROWSER_EXECUTABLE") or os.getenv(
            "OPEN_GROCERY_BROWSER_EXECUTABLE"
        )
        channel = os.getenv(prefix + "BROWSER_CHANNEL") or os.getenv(
            "OPEN_GROCERY_BROWSER_CHANNEL"
        )
        kwargs: dict[str, Any] = {"headless": headless}
        if executable:
            kwargs["executable_path"] = executable
        elif channel:
            kwargs["channel"] = channel
        return kwargs

    @contextmanager
    def _page(self, *, headless: bool | None = None, require_state: bool = True):
        if require_state and not self.state_path.exists():
            raise AuthenticationRequired(
                f"no local {self.config.label} session; run login_with_browser first"
            )
        sync_playwright = self._playwright()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        captured: list[Any] = []
        with self._mutex, sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    **self._launch_kwargs(headless=self._headless() if headless is None else headless)
                )
            except Exception as exc:
                raise AuthenticationRequired(
                    "could not start Chromium; install a Playwright browser or configure "
                    f"OPEN_GROCERY_{self.config.key.upper()}_BROWSER_EXECUTABLE"
                ) from exc
            try:
                context_kwargs: dict[str, Any] = {
                    "locale": "es-ES",
                    "viewport": {"width": 1440, "height": 1000},
                }
                if self.state_path.exists():
                    context_kwargs["storage_state"] = str(self.state_path)
                context = browser.new_context(**context_kwargs)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)

                def capture(response: Any) -> None:
                    try:
                        content_type = response.headers.get("content-type", "")
                        if "json" not in content_type.casefold() or not _CAPTURE_RE.search(response.url):
                            return
                        captured.append(response.json())
                        if len(captured) > 100:
                            del captured[:-100]
                    except Exception:
                        return

                page.on("response", capture)
                try:
                    yield page, captured, context
                finally:
                    try:
                        context.storage_state(path=str(self.state_path))
                        self._protect(self.state_path)
                    except Exception:
                        # Preserve the original retailer/browser exception. A failed
                        # state refresh must never turn a successful write into an
                        # automatic retry.
                        pass
            finally:
                browser.close()

    @staticmethod
    def _protect(path: Path) -> None:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def login(self, *, timeout_seconds: int) -> dict[str, Any]:
        if timeout_seconds < 30 or timeout_seconds > 900:
            raise InvalidRequest("timeout_seconds must be between 30 and 900")
        sync_playwright = self._playwright()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        completed = threading.Event()
        with self._mutex, sync_playwright() as playwright:
            browser = playwright.chromium.launch(**self._launch_kwargs(headless=False))
            try:
                context = browser.new_context(locale="es-ES")
                page = context.new_page()
                page.expose_function("__openGroceryLoginComplete", lambda: completed.set())
                context.add_init_script(
                    """
                    (() => {
                      const install = () => {
                        if (document.getElementById('__open_grocery_save_session')) return;
                        const button = document.createElement('button');
                        button.id = '__open_grocery_save_session';
                        button.textContent = 'Open Grocery: guardar sesión';
                        Object.assign(button.style, {
                          position: 'fixed', right: '18px', bottom: '18px', zIndex: '2147483647',
                          padding: '14px 18px', border: '0', borderRadius: '10px',
                          background: '#111', color: '#fff', fontSize: '15px',
                          fontFamily: 'system-ui,sans-serif', cursor: 'pointer', boxShadow: '0 4px 20px #0006'
                        });
                        button.addEventListener('click', async () => {
                          button.textContent = 'Sesión guardada';
                          button.disabled = true;
                          await window.__openGroceryLoginComplete();
                        });
                        document.documentElement.appendChild(button);
                      };
                      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
                      else install();
                    })();
                    """
                )
                page.goto(self.config.base_url, wait_until="domcontentloaded")
                deadline = time.monotonic() + timeout_seconds
                while not completed.is_set() and time.monotonic() < deadline:
                    page.wait_for_timeout(250)
                if not completed.is_set():
                    raise AuthenticationRequired(
                        "login was not confirmed before the timeout; sign in and click the black "
                        "'Open Grocery: guardar sesión' button"
                    )
                context.storage_state(path=str(self.state_path))
                self._protect(self.state_path)
            finally:
                browser.close()
        return {"store": self.config.key, "session_saved": True, "state_path": str(self.state_path)}

    @staticmethod
    def _regex(patterns: Sequence[str]) -> re.Pattern[str]:
        return re.compile("(?:" + "|".join(patterns) + ")", re.I)

    def _click_patterns(
        self,
        page: Any,
        patterns: Sequence[str],
        *,
        roles: Sequence[str] = ("button", "link"),
        required: bool = False,
    ) -> bool:
        expression = self._regex(patterns)
        for role in roles:
            try:
                locator = page.get_by_role(role, name=expression).filter(visible=True)
            except TypeError:  # Older Playwright has no visible filter keyword.
                locator = page.get_by_role(role, name=expression)
            try:
                if locator.count() and locator.first.is_visible():
                    locator.first.click()
                    return True
            except Exception:
                continue
        try:
            locator = page.locator("button,a,[role='button']").filter(has_text=expression)
            if locator.count() and locator.first.is_visible():
                locator.first.click()
                return True
        except Exception:
            pass
        if required:
            raise ProviderError(
                f"could not find a {self.config.label} control matching: {', '.join(patterns)}"
            )
        return False

    def _accept_cookies(self, page: Any) -> None:
        self._click_patterns(
            page,
            (r"aceptar todas", r"aceptar cookies", r"permitir todas", r"accept all"),
            required=False,
        )

    def _page_is_not_found(self, page: Any) -> bool:
        try:
            text = normalized_text(page.locator("body").inner_text(timeout=2500))
        except Exception:
            return False
        return any(token in text for token in ("pagina no encontrada", "página no encontrada", "404 not found"))

    def _goto_paths(self, page: Any, paths: Sequence[str]) -> bool:
        for path in paths:
            try:
                response = page.goto(urljoin(self.config.base_url, path), wait_until="domcontentloaded")
                if response is not None and response.status >= 400:
                    continue
                if not self._page_is_not_found(page):
                    self._accept_cookies(page)
                    return True
            except Exception:
                continue
        return False

    def _goto_cart(self, page: Any) -> None:
        page.goto(self.config.base_url, wait_until="domcontentloaded")
        self._accept_cookies(page)
        if self._click_patterns(page, self.config.cart_patterns):
            page.wait_for_timeout(700)
            return
        for selector in (
            'a[href*="/cart" i]',
            'a[href*="/cesta" i]',
            'a[href*="/carrito" i]',
            'button[aria-label*="cesta" i]',
            'button[aria-label*="carrito" i]',
        ):
            try:
                target = page.locator(selector)
                if target.count() and target.first.is_visible():
                    target.first.click()
                    page.wait_for_timeout(700)
                    return
            except Exception:
                continue
        if self._goto_paths(page, self.config.cart_paths):
            return
        raise ProviderError(f"could not open the {self.config.label} cart")

    def _goto_account(self, page: Any) -> None:
        page.goto(self.config.base_url, wait_until="domcontentloaded")
        self._accept_cookies(page)
        if self._click_patterns(page, self.config.account_patterns):
            page.wait_for_timeout(700)
            return
        if self._goto_paths(page, self.config.account_paths):
            return
        raise ProviderError(
            f"could not open the {self.config.label} account page; create a checkout first "
            "and retry address discovery from that confirmed checkout"
        )

    def _goto_checkout(self, page: Any) -> None:
        self._goto_cart(page)
        if self._click_patterns(page, self.config.checkout_patterns, required=False):
            page.wait_for_timeout(1000)
            return
        for selector in ('a[href*="checkout" i]', 'a[href*="finalizar" i]'):
            try:
                target = page.locator(selector)
                if target.count() and target.first.is_visible():
                    target.first.click()
                    page.wait_for_timeout(1000)
                    return
            except Exception:
                continue
        if self._goto_paths(page, self.config.checkout_paths):
            return
        raise ProviderError(f"could not open the {self.config.label} checkout")

