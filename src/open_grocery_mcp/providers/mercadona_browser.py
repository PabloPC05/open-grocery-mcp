"""Interactive Mercadona browser login."""

from __future__ import annotations

import json
import os
import stat
import time
from typing import Any

from open_grocery_mcp.errors import AuthenticationRequired, InvalidRequest
from open_grocery_mcp.providers.mercadona_state import _BASE_URL


class MercadonaBrowserMixin:

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
            except Exception as exc:
                raise AuthenticationRequired(f'could not open {channel}; install Google Chrome or configure the browser channel') from exc
            try:
                context = browser.new_context()
                page = context.new_page()
                detected = False

                def observe(request: Any) -> None:
                    nonlocal detected
                    if '/api/' not in request.url:
                        return
                    authorization = request.headers.get('authorization', '')
                    if authorization.startswith('Bearer '):
                        detected = True
                page.on('request', observe)
                page.goto(_BASE_URL, wait_until='domcontentloaded')
                deadline = time.monotonic() + timeout_seconds
                while not detected and time.monotonic() < deadline:
                    try:
                        value = page.evaluate("localStorage.getItem('MO-user')")
                        if value and json.loads(value).get('token'):
                            detected = True
                            break
                    except Exception:
                        pass
                    page.wait_for_timeout(500)
                if not detected:
                    raise AuthenticationRequired('no completed Mercadona login was detected before the timeout')
                context.storage_state(path=str(self.state_path))
            finally:
                browser.close()
        try:
            os.chmod(self.state_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return self.status()
