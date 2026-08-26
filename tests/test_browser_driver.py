import json
from contextlib import contextmanager
from pathlib import Path
import shutil

import pytest

from open_grocery_mcp.errors import AuthenticationRequired, ProviderError
from open_grocery_mcp.providers.browser_config import BrowserStoreConfig
from open_grocery_mcp.providers.browser_config import GADIS_BROWSER_CONFIG
from open_grocery_mcp.providers.browser_driver import PlaywrightBrowserDriver


CONFIG = BrowserStoreConfig(
    key="demo",
    label="Demo",
    base_url="https://demo.test",
    cart_paths=("/cart",),
)


def make_driver(tmp_path: Path) -> PlaywrightBrowserDriver:
    return PlaywrightBrowserDriver(
        CONFIG,
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )


def test_captured_cart_prefers_newest_response_after_removal(tmp_path):
    driver = make_driver(tmp_path)
    before = {
        "cart": {
            "lines": [
                {"product_id": "1", "name": "Leche", "quantity": 1, "unit_price": 1},
                {"product_id": "2", "name": "Pan", "quantity": 1, "unit_price": 2},
            ],
            "total": 3,
        }
    }
    after = {
        "cart": {
            "lines": [
                {"product_id": "1", "name": "Leche", "quantity": 1, "unit_price": 1}
            ],
            "total": 1,
        }
    }
    cart = driver._captured_cart([before, after])
    assert cart is not None
    assert cart["products_count"] == 1
    assert cart["total"] == 1.0


def test_human_handoff_only_navigates_and_never_clicks(tmp_path, monkeypatch) -> None:
    driver = make_driver(tmp_path)
    calls: list[str] = []
    routed: list[str] = []

    class Response:
        status = 200

    class Page:
        url = "https://demo.test/cart?private=value"

        def goto(self, url, *, wait_until):
            assert wait_until == "domcontentloaded"
            calls.append(url)
            self.url = url + "?private=value"
            return Response()

        def is_closed(self):
            return True

    class Context:
        def route(self, pattern, handler):
            assert pattern == "**/*"

            class Request:
                method = "POST"

            class Route:
                request = Request()

                def abort(self):
                    routed.append("aborted")

                def continue_(self):
                    routed.append("continued")

            handler(Route())

    @contextmanager
    def fake_page(*, headless):
        assert headless is False
        yield Page(), [], Context()

    monkeypatch.setattr(driver, "_page", fake_page)
    monkeypatch.setattr(driver, "_page_is_not_found", lambda _page: False)

    result = driver.open_human_handoff(timeout_seconds=30)

    assert calls == ["https://demo.test/cart"]
    assert result["review_url"] == "https://demo.test/cart"
    assert result["automated_navigation_method"] == "GET"
    assert result["automated_clicks"] == 0
    assert result["non_get_requests_blocked"] == 1
    assert result["network_write_guard"] == "all_non_get_blocked"
    assert routed == ["aborted"]
    assert result["automated_order_submission"] is False


def test_checkout_handoff_rejects_redirect_to_login(tmp_path, monkeypatch) -> None:
    config = BrowserStoreConfig(
        key="demo",
        label="Demo",
        base_url="https://demo.test",
        cart_paths=("/cart",),
        checkout_paths=("/checkout",),
    )
    driver = PlaywrightBrowserDriver(
        config,
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )

    class Response:
        status = 200

    class Page:
        url = "https://demo.test/login"

        def goto(self, _url, *, wait_until):
            assert wait_until == "domcontentloaded"
            return Response()

    class Context:
        def route(self, _pattern, _handler):
            pass

    @contextmanager
    def fake_page(*, headless):
        assert headless is False
        yield Page(), [], Context()

    monkeypatch.setattr(driver, "_page", fake_page)
    monkeypatch.setattr(driver, "_page_is_not_found", lambda _page: False)

    with pytest.raises(ProviderError, match="could not open a safe"):
        driver.open_human_handoff(checkout_review=True, timeout_seconds=30)


def test_human_handoff_refuses_external_preferred_url(tmp_path, monkeypatch) -> None:
    driver = make_driver(tmp_path)

    @contextmanager
    def fake_page(*, headless):
        del headless
        yield object(), [], object()

    monkeypatch.setattr(driver, "_page", fake_page)
    with pytest.raises(ProviderError, match="outside the retailer domain"):
        driver.open_human_handoff(
            preferred_url="https://evil.example/checkout",
            timeout_seconds=30,
        )


def test_dom_cart_script_with_real_chromium(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    driver = make_driver(tmp_path)
    with playwright.sync_playwright() as runtime:
        executable = (
            shutil.which("chromium")
            or shutil.which("google-chrome")
            or runtime.chromium.executable_path
        )
        if not Path(executable).is_file():
            pytest.skip("no Chromium executable installed")
        browser = runtime.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page()
            page.set_content(
                """
                <main>
                  <article class="cart-item" data-product-id="milk-1">
                    <a href="https://demo.test/product/milk-1">Leche entera 1 L</a>
                    <span>1,25 €</span>
                    <input type="number" name="quantity" value="2" />
                    <button aria-label="Eliminar producto">x</button>
                  </article>
                  <div class="cart-total">Total 2,50 €</div>
                </main>
                """
            )
            cart = driver._dom_cart(page)
        finally:
            browser.close()

    assert cart["products_count"] == 1
    assert cart["lines"][0]["product_id"] == "milk-1"
    assert cart["lines"][0]["quantity"] == 2.0
    assert cart["total_text"] == "2.50"


def test_gadis_checkout_review_uses_observed_safe_get_path() -> None:
    assert GADIS_BROWSER_CONFIG.checkout_paths[0] == (
        "/pag/proceso-de-compra/compra-segura"
    )


def test_eroski_add_uses_the_exact_search_tile(tmp_path):
    config = BrowserStoreConfig(
        key="eroski",
        label="Eroski",
        base_url="https://supermercado.eroski.es",
        cart_paths=("/es/mycart/?basketType=ALI",),
    )
    driver = PlaywrightBrowserDriver(
        config,
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )
    clicked = {"value": False}

    class Control:
        @property
        def first(self):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self):
            clicked["value"] = True

    class Page:
        def __init__(self):
            self.urls = []

        def goto(self, url, *, wait_until):
            self.urls.append((url, wait_until))

        def locator(self, selector):
            assert selector == "#item-list-12345 a.update.toAddProduct"
            return Control()

        def wait_for_timeout(self, _):
            return None

    page = Page()

    def accept_cookies(_):
        return None

    driver._accept_cookies = accept_cookies
    driver._add_product(
        page,
        {
            "product_id": "12345",
            "name": "Leche entera",
            "url": "https://supermercado.eroski.es/es/productdetail/12345-leche/",
        },
    )
    assert page.urls == [
        (
            "https://supermercado.eroski.es/es/search/results/?q=12345",
            "domcontentloaded",
        )
    ]
    assert clicked["value"] is True


def make_froiz_driver(tmp_path: Path) -> PlaywrightBrowserDriver:
    return PlaywrightBrowserDriver(
        BrowserStoreConfig(
            key="froiz",
            label="Froiz",
            base_url="https://supermercado.froiz.com",
            cart_paths=("/cesta",),
        ),
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )


class _LoginLocator:
    def __init__(self, text: str, count: int = 0) -> None:
        self.text = text
        self._count = count

    def inner_text(self, **_kwargs):
        return self.text

    def count(self):
        return self._count


class _LoginPage:
    def __init__(
        self,
        body: str,
        *,
        password_fields: int = 0,
        authenticated_controls: int = 0,
    ) -> None:
        self.body = body
        self.password_fields = password_fields
        self.authenticated_controls = authenticated_controls
        self.url = "https://supermercado.froiz.com/es/login/"

    def locator(self, selector: str):
        if selector == "body":
            return _LoginLocator(self.body)
        if selector == "input[type='password']":
            return _LoginLocator("", self.password_fields)
        return _LoginLocator("", self.authenticated_controls)

    def goto(self, _url, *, wait_until):
        assert wait_until == "domcontentloaded"


class _LoginContext:
    def __init__(self, page: _LoginPage) -> None:
        self.page = page
        self.handlers = {}
        self.init_scripts = []
        self.storage_state_calls = 0

    def new_page(self):
        return self.page

    def on(self, event, callback):
        self.handlers[event] = callback

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def storage_state(self, *, path):
        self.storage_state_calls += 1
        Path(path).write_text(json.dumps({"cookies": [], "origins": []}))


class _LoginBrowser:
    def __init__(self, context: _LoginContext) -> None:
        self.context = context
        self.new_context_kwargs = None

    def new_context(self, **kwargs):
        self.new_context_kwargs = kwargs
        return self.context

    def close(self):
        return None


class _LoginChromium:
    def __init__(self, browser: _LoginBrowser) -> None:
        self.browser = browser

    def launch(self, **_kwargs):
        return self.browser


class _LoginRuntime:
    def __init__(self, chromium: _LoginChromium) -> None:
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _LoginSyncPlaywright:
    def __init__(self, runtime: _LoginRuntime) -> None:
        self.runtime = runtime

    def __call__(self):
        return self.runtime


def test_froiz_login_saves_only_after_read_only_validation(tmp_path, monkeypatch):
    page = _LoginPage("Iniciar sesión", password_fields=1)
    context = _LoginContext(page)
    runtime = _LoginRuntime(_LoginChromium(_LoginBrowser(context)))
    driver = make_froiz_driver(tmp_path)
    monkeypatch.setattr(driver, "_playwright", lambda: _LoginSyncPlaywright(runtime))
    ticks = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr("open_grocery_mcp.providers.browser_driver_core.time.monotonic", lambda: next(ticks))

    def advance(_milliseconds):
        page.body = "Hola. Cerrar sesión"
        page.password_fields = 0
        page.authenticated_controls = 1

    page.wait_for_timeout = advance
    page.evaluate = lambda script: (
        "https://supermercado.froiz.com"
        if "location.origin" in script
        else {"session-key": "session-value"}
    )
    result = driver.login(timeout_seconds=30)

    assert result["validated_live"] is True
    assert result["authenticated_session"] is True
    assert context.init_scripts == []
    assert context.storage_state_calls == 1
    assert json.loads((tmp_path / "session_storage.json").read_text())["https://supermercado.froiz.com"]


def test_froiz_login_reuses_valid_local_state_and_session_storage(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    (tmp_path / "session_storage.json").write_text(
        json.dumps({"https://supermercado.froiz.com": {"session-key": "session-value"}}),
        encoding="utf-8",
    )
    page = _LoginPage("Hola. Cerrar sesión", authenticated_controls=1)
    page.wait_for_timeout = lambda _milliseconds: None
    page.evaluate = lambda script: (
        "https://supermercado.froiz.com"
        if "location.origin" in script
        else {"session-key": "session-value"}
    )
    context = _LoginContext(page)
    browser = _LoginBrowser(context)
    runtime = _LoginRuntime(_LoginChromium(browser))
    driver = make_froiz_driver(tmp_path)
    monkeypatch.setattr(driver, "_playwright", lambda: _LoginSyncPlaywright(runtime))
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(
        "open_grocery_mcp.providers.browser_driver_core.time.monotonic",
        lambda: next(ticks),
    )

    result = driver.login(timeout_seconds=30)

    assert result["validated_live"] is True
    assert browser.new_context_kwargs == {
        "locale": "es-ES",
        "storage_state": str(state_path),
    }
    assert context.init_scripts


def test_froiz_login_does_not_write_state_without_validation(tmp_path, monkeypatch):
    page = _LoginPage("Iniciar sesión", password_fields=1)
    context = _LoginContext(page)
    runtime = _LoginRuntime(_LoginChromium(_LoginBrowser(context)))
    driver = make_froiz_driver(tmp_path)
    monkeypatch.setattr(driver, "_playwright", lambda: _LoginSyncPlaywright(runtime))
    ticks = iter((0.0, 31.0))
    monkeypatch.setattr("open_grocery_mcp.providers.browser_driver_core.time.monotonic", lambda: next(ticks))
    page.wait_for_timeout = lambda _milliseconds: None

    with pytest.raises(AuthenticationRequired, match="read-only authenticated-page check"):
        driver.login(timeout_seconds=30)

    assert context.storage_state_calls == 0
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "session_storage.json").exists()


def test_login_validation_ignores_url_only_changes(tmp_path):
    driver = make_froiz_driver(tmp_path)
    page = _LoginPage("Identificación de cliente", password_fields=1)
    page.url = "https://supermercado.froiz.com/es/mi-cuenta/"
    assert driver._page_has_authenticated_session(page) is False


def test_login_validation_accepts_read_only_authenticated_dom(tmp_path):
    driver = make_froiz_driver(tmp_path)
    page = _LoginPage("Hola. Cerrar sesión", authenticated_controls=1)
    page.url = "https://supermercado.froiz.com/es/mi-cuenta/"
    assert driver._page_has_authenticated_session(page) is True


def test_gadis_login_uses_read_only_dom_detection_without_legacy_button(
    tmp_path, monkeypatch
):
    config = BrowserStoreConfig(
        key="gadis",
        label="Gadis",
        base_url="https://www.gadisline.com",
        cart_paths=("/cart",),
    )
    driver = PlaywrightBrowserDriver(
        config,
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )
    page = _LoginPage("Hola. Cerrar sesiÃ³n", authenticated_controls=1)
    page.wait_for_timeout = lambda _milliseconds: None
    page.evaluate = lambda _script: {}
    context = _LoginContext(page)
    runtime = _LoginRuntime(_LoginChromium(_LoginBrowser(context)))
    monkeypatch.setattr(driver, "_playwright", lambda: _LoginSyncPlaywright(runtime))
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(
        "open_grocery_mcp.providers.browser_driver_core.time.monotonic",
        lambda: next(ticks),
    )

    result = driver.login(timeout_seconds=30)

    assert result["validated_live"] is True
    assert result["validation"] == "read_only_authenticated_dom"
    assert context.storage_state_calls == 1


def test_gadis_login_accepts_only_authenticated_nextauth_session_response(tmp_path):
    config = BrowserStoreConfig(
        key="gadis",
        label="Gadis",
        base_url="https://www.gadisline.com",
        cart_paths=("/cart",),
    )
    driver = PlaywrightBrowserDriver(
        config,
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )

    class Response:
        url = "https://www.gadisline.com/api/auth/session"
        status = 200
        request = type("Request", (), {"method": "GET"})()

        @staticmethod
        def json():
            return {
                "user": {"id": "placeholder"},
                "expires": "2099-01-01T00:00:00Z",
                "token": {"accessToken": "placeholder"},
            }

    assert driver._gadis_response_is_authenticated(Response()) is True
    Response.request.method = "GET"
    Response.url = "https://www.gadisline.com/api/auth/session"
    Response.json = staticmethod(
        lambda: {"user": {"id": "placeholder"}, "token": {"accessToken": True}}
    )
    assert driver._gadis_response_is_authenticated(Response()) is False
    Response.request.method = "POST"
    assert driver._gadis_response_is_authenticated(Response()) is False
    Response.request.method = "GET"
    Response.url = "https://evil.example/api/auth/session"
    assert driver._gadis_response_is_authenticated(Response()) is False


def test_gadis_login_finishes_from_nextauth_response_without_logout_control(
    tmp_path, monkeypatch
):
    config = BrowserStoreConfig(
        key="gadis",
        label="Gadis",
        base_url="https://www.gadisline.com",
        cart_paths=("/cart",),
    )
    driver = PlaywrightBrowserDriver(
        config,
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )

    class Response:
        url = "https://www.gadisline.com/api/auth/session"
        status = 200
        request = type("Request", (), {"method": "GET"})()

        @staticmethod
        def json():
            return {"user": {"id": "placeholder"}, "token": {"accessToken": "placeholder"}}

    class Page(_LoginPage):
        def __init__(self):
            super().__init__("Sign in", password_fields=1)
            self.handlers = {}

        def on(self, event, callback):
            self.handlers[event] = callback

        def goto(self, _url, *, wait_until):
            assert wait_until == "domcontentloaded"
            self.handlers["response"](Response())

        def wait_for_timeout(self, _milliseconds):
            return None

    page = Page()
    context = _LoginContext(page)
    runtime = _LoginRuntime(_LoginChromium(_LoginBrowser(context)))
    monkeypatch.setattr(driver, "_playwright", lambda: _LoginSyncPlaywright(runtime))

    result = driver.login(timeout_seconds=30)

    assert result["validated_live"] is True
    assert result["validation"] == "gadis_get_auth_session_2xx"
    assert context.storage_state_calls == 1


def test_login_validation_rejects_generic_guest_welcome_copy(tmp_path):
    driver = make_froiz_driver(tmp_path)
    page = _LoginPage("Bienvenida. Descubre nuestra tienda y sus ofertas")
    assert driver._page_has_authenticated_session(page) is False


def test_froiz_login_opens_only_the_exact_login_entry(tmp_path):
    clicked: list[str] = []

    class Candidate:
        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, *, timeout):
            assert timeout == 3000
            clicked.append("login")

    class Page:
        def locator(self, selector):
            assert selector == 'a:has-text("Identif")'
            return Candidate()

    driver = make_froiz_driver(tmp_path)
    assert driver._open_login_entry(Page()) is True
    assert clicked == ["login"]


@pytest.mark.parametrize(
    ("url", "method", "status", "payload", "expected"),
    [
        (
            "https://servicios.froiz.com/api/me",
            "GET",
            200,
            {"id": "user-1", "userChannelOptions": []},
            True,
        ),
        (
            "https://servicios.froiz.com/api/me",
            "GET",
            200,
            {"authenticated": False, "userChannelOptions": []},
            False,
        ),
        (
            "https://servicios.froiz.com/api/me",
            "GET",
            401,
            {"id": "user-1", "userChannelOptions": []},
            False,
        ),
        (
            "https://servicios.froiz.com/api/cart",
            "GET",
            200,
            {"id": "user-1", "userChannelOptions": []},
            False,
        ),
    ],
)
def test_froiz_login_response_requires_authenticated_me_shape(
    tmp_path, url, method, status, payload, expected
):
    class Response:
        request = type(
            "Request",
            (),
            {
                "method": method,
                "headers": {"authorization": "Bearer candidate"},
            },
        )()

        def __init__(self):
            self.url = url
            self.status = status

        def json(self):
            return payload

    driver = make_froiz_driver(tmp_path)
    assert driver._froiz_response_is_authenticated(Response()) is expected


def test_froiz_login_candidate_requires_trusted_read_only_api_request(tmp_path) -> None:
    driver = make_froiz_driver(tmp_path)

    class Request:
        url = "https://servicios.froiz.com/api/config"
        method = "GET"
        headers = {"authorization": "Bearer candidate"}

    assert driver._froiz_request_token_candidate(Request()) == "candidate"
    Request.url = "https://evil.example/api/config"
    assert driver._froiz_request_token_candidate(Request()) is None
    Request.url = "https://servicios.froiz.com/api/cart"
    Request.method = "POST"
    assert driver._froiz_request_token_candidate(Request()) is None


def test_froiz_login_response_requires_a_bearer_header(tmp_path) -> None:
    driver = make_froiz_driver(tmp_path)

    class Response:
        url = "https://servicios.froiz.com/api/me"
        status = 200
        request = type("Request", (), {"method": "GET", "headers": {}})()

        @staticmethod
        def json():
            return {"id": "user-1", "userChannelOptions": []}

    assert driver._froiz_response_is_authenticated(Response()) is False


def test_froiz_login_candidate_is_accepted_only_after_me_validation(tmp_path) -> None:
    driver = make_froiz_driver(tmp_path)

    class Page:
        url = "https://supermercado.froiz.com/es/login/"

        def __init__(self, result: bool) -> None:
            self.result = result

        def evaluate(self, script: str, token: str) -> bool:
            assert "https://servicios.froiz.com/api/me" in script
            assert "credentials: 'omit'" in script
            assert token == "candidate"
            return self.result

    assert driver._froiz_token_is_authenticated(Page(True), "candidate") is True
    assert driver._froiz_token_is_authenticated(Page(False), "candidate") is False


def test_froiz_login_reads_only_exact_auth_keys_on_trusted_origin(tmp_path) -> None:
    driver = make_froiz_driver(tmp_path)

    class Page:
        def __init__(self, origin: str) -> None:
            self.origin = origin
            self.calls = 0

        def evaluate(self, script: str):
            self.calls += 1
            if "location.origin" in script:
                return self.origin
            assert "auth._token.froiz" in script
            assert "auth._token.local" in script
            return ["Bearer candidate", "candidate", "bad token"]

    trusted = Page("https://supermercado.froiz.com")
    assert driver._froiz_storage_token_candidates(trusted) == ("candidate",)
    external = Page("https://evil.example")
    assert driver._froiz_storage_token_candidates(external) == ()
    assert external.calls == 1


def test_froiz_login_does_not_pass_token_to_an_external_popup(tmp_path) -> None:
    driver = make_froiz_driver(tmp_path)

    class Page:
        url = "https://accounts.example.test/login"

        def evaluate(self, *_args):
            raise AssertionError("external page must not receive the bearer")

    assert driver._froiz_token_is_authenticated(Page(), "candidate") is False


def test_checkout_navigation_never_uses_ambiguous_submit_labels(tmp_path) -> None:
    driver = make_driver(tmp_path)

    def goto_cart(_page):
        return None

    driver._goto_cart = goto_cart

    def forbidden_patterns(*args, **kwargs):
        raise AssertionError("checkout text patterns must never be clicked")

    driver._click_patterns = forbidden_patterns

    class EmptyLocator:
        def count(self):
            return 0

    class Page:
        def locator(self, selector):
            return EmptyLocator()

    with pytest.raises(ProviderError, match="could not open"):
        driver._goto_checkout(Page())
