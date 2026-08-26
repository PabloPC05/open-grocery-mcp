"""Interactive Mercadona browser login."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlsplit

from open_grocery_mcp.errors import AuthenticationRequired, InvalidRequest
from open_grocery_mcp.providers.browser_config import MERCADONA_BROWSER_CONFIG
from open_grocery_mcp.providers.browser_driver import PlaywrightBrowserDriver
from open_grocery_mcp.providers.mercadona_state import _BASE_URL


class MercadonaBrowserMixin:

    def open_human_review(
        self,
        *,
        checkout_id: str | None = None,
        checkout_review: bool = False,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Open Mercadona's cart/checkout UI; the agent performs no clicks."""

        del checkout_id
        driver = PlaywrightBrowserDriver(
            MERCADONA_BROWSER_CONFIG,
            state_path=Path(self.state_path),
            checkout_store=Path(self.state_path).with_name("checkouts-browser.json"),
        )
        return driver.open_human_handoff(
            checkout_review=checkout_review,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _trusted_storefront_page(page: Any) -> bool:
        """Accept probes only from the HTTPS Mercadona storefront origin."""
        raw_url = getattr(page, 'url', '')
        if callable(raw_url):
            raw_url = raw_url()
        if not raw_url:
            # Small test doubles may not expose Playwright's ``page.url``.
            return True
        parsed = urlsplit(str(raw_url))
        expected = urlsplit(_BASE_URL)
        return (
            parsed.scheme.casefold() == expected.scheme.casefold() == 'https'
            and parsed.hostname
            and parsed.hostname.casefold() == (expected.hostname or '').casefold()
            and (parsed.port or 443) == (expected.port or 443)
        )

    def _save_browser_storage_state(self, context: Any) -> None:
        """Atomically publish private Playwright state after live validation."""

        destination = Path(self.state_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                'w', encoding='utf-8', dir=destination.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
            context.storage_state(path=str(temporary))
            try:
                os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            temporary.replace(destination)
            try:
                os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _session_cart_probe(page: Any) -> bool:
        """Perform an authenticated, read-only cart request in the page.

        The token is read and used inside the browser context only.  Returning
        a boolean keeps credentials, customer identifiers and response bodies
        out of Python logs and the login result.
        """
        if not MercadonaBrowserMixin._trusted_storefront_page(page):
            return False
        result = page.evaluate(
            """
            async () => {
              try {
                const raw = localStorage.getItem('MO-user');
                if (!raw) return false;
                const user = JSON.parse(raw);
                const customer = user.uuid || user.customer_id || user.customer_uuid;
                const token = user.token || user.access_token;
                if (!customer) return false;
                const headers = token ? { Authorization: `Bearer ${token}` } : {};
                const customerResponse = await fetch(
                  `/api/customers/${encodeURIComponent(customer)}/`,
                  { method: 'GET', credentials: 'include', headers },
                );
                if (customerResponse.status < 200 || customerResponse.status >= 300) {
                  return false;
                }
                const cartResponse = await fetch(
                  `/api/customers/${encodeURIComponent(customer)}/cart/`,
                  { method: 'GET', credentials: 'include', headers },
                );
                return cartResponse.status >= 200 && cartResponse.status < 300;
              } catch (_) {
                return false;
              }
            }
            """
        )
        return result is True

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        if timeout_seconds < 30 or timeout_seconds > 900:
            raise InvalidRequest('timeout_seconds must be between 30 and 900')
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AuthenticationRequired('browser login requires `pip install "open-grocery-mcp[browser]"`') from exc
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        channel = os.getenv('OPEN_GROCERY_MERCADONA_BROWSER_CHANNEL', 'chrome')
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=False, channel=channel)
            except Exception as configured_error:
                try:
                    browser = playwright.chromium.launch(headless=False)
                except Exception as bundled_error:
                    raise AuthenticationRequired(
                        "Mercadona browser launch failed for the configured channel "
                        "and bundled Chromium "
                        f"(configured={type(configured_error).__name__}, "
                        f"bundled={type(bundled_error).__name__})"
                    ) from None
            try:
                context_kwargs: dict[str, Any] = {}
                if self.state_path.is_file():
                    try:
                        saved_state = json.loads(
                            self.state_path.read_text(encoding='utf-8')
                        )
                    except (OSError, ValueError):
                        saved_state = None
                    if (
                        isinstance(saved_state, dict)
                        and isinstance(saved_state.get('cookies', []), list)
                        and isinstance(saved_state.get('origins', []), list)
                    ):
                        context_kwargs['storage_state'] = str(self.state_path)
                context = browser.new_context(**context_kwargs)
                detected = False
                validated = False

                def observe(request: Any) -> None:
                    nonlocal detected
                    parsed = urlsplit(str(request.url))
                    expected = urlsplit(_BASE_URL)
                    if (
                        parsed.scheme.casefold() != 'https'
                        or not parsed.hostname
                        or parsed.hostname.casefold()
                        != (expected.hostname or '').casefold()
                        or '/api/' not in parsed.path
                    ):
                        return
                    authorization = request.headers.get('authorization', '')
                    if authorization.startswith('Bearer '):
                        detected = True

                def setup_page(candidate: Any) -> None:
                    candidate.on('request', observe)

                page = context.new_page()
                setup_page(page)
                # Register before navigation so OAuth popups and any
                # retailer-created pages cannot escape capture.
                context.on('page', setup_page)
                page.goto(_BASE_URL, wait_until='domcontentloaded')
                deadline = time.monotonic() + timeout_seconds
                while not validated and time.monotonic() < deadline:
                    for candidate in list(context.pages):
                        try:
                            has_user = candidate.evaluate(
                                """
                                () => {
                                  try {
                                    const raw = localStorage.getItem('MO-user');
                                    const user = raw ? JSON.parse(raw) : {};
                                    return Boolean(user.token || user.access_token);
                                  } catch (_) { return false; }
                                }
                                """
                            )
                            if has_user:
                                detected = True
                                validated = self._session_cart_probe(candidate)
                                if validated:
                                    break
                        except Exception:
                            pass
                    if validated:
                        break
                    page.wait_for_timeout(500)
                if not detected:
                    raise AuthenticationRequired('no completed Mercadona login was detected before the timeout')
                if not validated:
                    raise AuthenticationRequired('Mercadona login was not validated by a customer/cart read before the timeout')
                self._save_browser_storage_state(context)
            finally:
                browser.close()
        result = self.status()
        result['authentication_checked_live'] = True
        result['authenticated'] = True
        result['validated_live'] = True
        return result
