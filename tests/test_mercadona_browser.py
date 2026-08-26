from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from open_grocery_mcp.errors import AuthenticationRequired
from open_grocery_mcp.providers.mercadona_browser import MercadonaBrowserMixin


class _Page:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.evaluations: list[str] = []

    def on(self, event: str, callback: object) -> None:
        self.handlers[event] = callback

    def goto(self, *_args, **_kwargs) -> None:
        return None

    def evaluate(self, script: str) -> bool:
        self.evaluations.append(script)
        return True

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class _Context:
    def __init__(self) -> None:
        self.page = _Page()
        self.pages = [self.page]
        self.handlers: dict[str, object] = {}
        self.saved_path: str | None = None

    def new_page(self) -> _Page:
        return self.page

    def on(self, event: str, callback: object) -> None:
        self.handlers[event] = callback

    def storage_state(self, *, path: str) -> None:
        self.saved_path = path
        Path(path).write_text('{}', encoding='utf-8')

    def close(self) -> None:
        return None


class _Browser:
    def __init__(self) -> None:
        self.context = _Context()
        self.new_context_kwargs = None

    def new_context(self, **kwargs):
        self.new_context_kwargs = kwargs
        return self.context

    def close(self) -> None:
        return None


class _Playwright:
    def __init__(self) -> None:
        self.browser = _Browser()
        self.chromium = self
        self.launches: list[dict[str, object]] = []

    def launch(self, **kwargs):
        self.launches.append(kwargs)
        return self.browser

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class _Client(MercadonaBrowserMixin):
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def status(self):
        return {'authenticated': False, 'authentication_checked_live': False}


def test_login_requires_and_performs_read_only_customer_cart_probe(
    tmp_path: Path, monkeypatch
) -> None:
    playwright = _Playwright()
    module = types.ModuleType('playwright.sync_api')
    module.sync_playwright = lambda: playwright
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', module)

    client = _Client(tmp_path / 'storage_state.json')
    result = client.login_with_browser(timeout_seconds=30)

    assert result['authenticated'] is True
    assert result['authentication_checked_live'] is True
    assert result['validated_live'] is True
    assert playwright.browser.context.saved_path is not None
    assert any(
        '/api/customers/' in script
        and '/cart/' in script
        and 'customerResponse' in script
        and 'cartResponse' in script
        for script in playwright.browser.context.page.evaluations
    )
    assert 'page' in playwright.browser.context.handlers
    assert playwright.launches == [{'headless': False, 'channel': 'chrome'}]


def test_login_falls_back_to_bundled_chromium_after_channel_launch_failure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv('OPEN_GROCERY_MERCADONA_BROWSER_CHANNEL', 'edge')
    playwright = _Playwright()
    channel_browser = RuntimeError("channel detail that must stay local")

    def launch(**kwargs):
        playwright.launches.append(kwargs)
        if 'channel' in kwargs:
            raise channel_browser
        return playwright.browser

    playwright.chromium.launch = launch
    module = types.ModuleType('playwright.sync_api')
    module.sync_playwright = lambda: playwright
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', module)

    result = _Client(tmp_path / 'storage_state.json').login_with_browser(
        timeout_seconds=30
    )

    assert result['validated_live'] is True
    assert playwright.launches == [
        {'headless': False, 'channel': 'edge'},
        {'headless': False},
    ]


def test_login_redacts_double_browser_launch_failure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv('OPEN_GROCERY_MERCADONA_BROWSER_CHANNEL', 'edge')
    playwright = _Playwright()

    def launch(**kwargs):
        playwright.launches.append(kwargs)
        if 'channel' in kwargs:
            raise RuntimeError('configured private launch detail')
        raise OSError('bundled private launch detail')

    playwright.chromium.launch = launch
    module = types.ModuleType('playwright.sync_api')
    module.sync_playwright = lambda: playwright
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', module)

    with pytest.raises(AuthenticationRequired) as error:
        _Client(tmp_path / 'storage_state.json').login_with_browser(
            timeout_seconds=30
        )

    message = str(error.value)
    assert 'configured private launch detail' not in message
    assert 'bundled private launch detail' not in message
    assert 'RuntimeError' in message
    assert 'OSError' in message
    assert playwright.launches == [
        {'headless': False, 'channel': 'edge'},
        {'headless': False},
    ]


def test_login_reuses_a_structurally_valid_local_storage_state(
    tmp_path: Path, monkeypatch
) -> None:
    playwright = _Playwright()
    module = types.ModuleType('playwright.sync_api')
    module.sync_playwright = lambda: playwright
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', module)
    state_path = tmp_path / 'storage_state.json'
    state_path.write_text('{"cookies": [], "origins": []}', encoding='utf-8')

    result = _Client(state_path).login_with_browser(timeout_seconds=30)

    assert result['validated_live'] is True
    assert playwright.browser.new_context_kwargs == {
        'storage_state': str(state_path)
    }


def test_session_cart_probe_does_not_return_page_secrets() -> None:
    page = _Page()

    assert MercadonaBrowserMixin._session_cart_probe(page) is True
    script = page.evaluations[-1]
    assert 'customerResponse.status' in script
    assert 'cartResponse.status' in script
    assert 'return token' not in script


def test_session_cart_probe_rejects_a_foreign_page_origin() -> None:
    class ForeignPage(_Page):
        url = 'https://evil.example/account'

    page = ForeignPage()

    assert MercadonaBrowserMixin._session_cart_probe(page) is False
    assert page.evaluations == []
