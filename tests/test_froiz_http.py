"""Mocked-transport tests for the Froiz HTTP client contract."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import httpx
import pytest

from open_grocery_mcp.errors import AuthenticationRequired
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient


def _state(tmp_path: Path) -> Path:
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    return path


def _processed_cart(cart_id: str, qty: float = 1) -> dict:
    return {
        "id": cart_id,
        "items": [
            {
                "comment": "",
                "enabled": True,
                "product": {"id": "p-milk", "name": "Leche", "price": 1.65},
                "qty": qty,
                "unit": "ud",
            }
        ],
        "total": round(1.65 * qty, 2),
        "userId": "<user>",
    }


def _router(
    requests: list[httpx.Request],
    *,
    raw_payload: dict | None = None,
    unauthorized_first: bool = False,
) -> httpx.MockTransport:
    state = {"unauthorized_seen": False}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/api/me":
            if unauthorized_first and not state["unauthorized_seen"]:
                state["unauthorized_seen"] = True
                return httpx.Response(401, text="Unauthorized")
            return httpx.Response(
                200,
                json={
                    "id": "user-1",
                    "userAddresses": [
                        {
                            "id": "addr-1",
                            "isDefault": True,
                            "postalCode": "28050",
                            "street": "Calle Secreta",
                        }
                    ],
                    "userChannelOptions": [
                        {"channelName": "shop", "cartId": "cart-uuid"}
                    ],
                },
            )
        if path.startswith("/api/stores/postalcode/"):
            return httpx.Response(
                200,
                json={
                    "id": "store-7",
                    "codEnt": "E1",
                    "codSubent": "S2",
                    "hasDelivery": True,
                },
            )
        if path.startswith("/api/deliverymatrix/calendar/"):
            assert path == "/api/deliverymatrix/calendar/E1_S2"
            return httpx.Response(
                200,
                json={
                    "deliveryCalendar": [
                        {
                            "date": "2026-08-22",
                            "active": True,
                            "slots": [
                                {"slotText": "10:00 - 12:00", "slotNumber": 0, "active": True},
                                {"slotText": "12:00 - 14:00", "slotNumber": 1, "active": False},
                            ],
                        }
                    ]
                },
            )
        if path == "/api/products" and request.method == "GET":
            assert request.url.params["term"] == "agua mineral 1 l"
            assert request.url.params["page"] == "1"
            assert request.url.params["size"] == "20"
            assert request.url.params["store"] == "E1_S2"
            return httpx.Response(
                200,
                json={
                    "products": [
                        {
                            "id": "p-water",
                            "name": "Agua mineral",
                            "enabled": True,
                            "fractional": False,
                            "per_unit": False,
                            "order_price": 0.75,
                        }
                    ],
                    "stats": {},
                },
            )
        if path.startswith("/api/cart/raw/"):
            payload = raw_payload if raw_payload is not None else _processed_cart("cart-uuid")
            return httpx.Response(200, json=payload)
        if path.startswith("/api/cart/") and request.method == "GET":
            cart_id = path.rsplit("/", 1)[-1]
            payload = raw_payload if raw_payload is not None else _processed_cart(cart_id)
            return httpx.Response(200, json=payload)
        if path == "/api/cart" and request.method == "POST":
            body = json.loads(request.content)
            cart = _processed_cart("new-cart")
            cart["items"] = [
                {**item, "enabled": True, "product": {"id": item["product_id"], "name": "Leche", "price": 1.65}}
                for item in body.get("items", [])
            ]
            return httpx.Response(201, json=cart)
        if path.startswith("/api/cart/") and request.method == "PUT":
            body = json.loads(request.content)
            cart_id = path.rsplit("/", 1)[-1]
            cart = _processed_cart(cart_id)
            cart["items"] = [
                {**item, "enabled": True, "product": {"id": item["product_id"], "name": "Leche", "price": 1.65}}
                for item in body.get("items", [])
            ]
            cart["total"] = round(sum(i["qty"] * 1.65 for i in cart["items"]), 2)
            return httpx.Response(200, json=cart)
        if path.startswith("/api/cart/") and request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _client(tmp_path: Path, transport: httpx.MockTransport) -> FroizHTTPClient:
    client = FroizHTTPClient(state_path=_state(tmp_path), client=httpx.Client(transport=transport))
    client._token_cache_path = tmp_path / "http_token.json"
    client._store_token("tok")
    return client


class _FakeBrowserRequest:
    def __init__(self, url: str, *, method: str = "GET", token: str = "fresh") -> None:
        self.url = url
        self.method = method
        self.headers = {"authorization": f"Bearer {token}"}


class _FakeBrowserResponse:
    def __init__(self, request: _FakeBrowserRequest, status: int, payload: object) -> None:
        self.request = request
        self.status = status
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeBrowserContext:
    def __init__(self, response_factory, *, token_validation: bool = False) -> None:
        self._listeners: dict[str, object] = {}
        self._response_factory = response_factory
        self._token_validation = token_validation

    def on(self, event: str, callback) -> None:
        self._listeners[event] = callback

    def new_page(self):
        context = self

        class Page:
            url = "https://supermercado.froiz.com/"

            def set_default_timeout(self, timeout: int) -> None:
                del timeout

            def goto(self, url: str, *, wait_until: str) -> None:
                del url, wait_until
                request, response = context._response_factory()
                context._listeners["request"](request)
                context._listeners["response"](response)

            def wait_for_timeout(self, timeout: int) -> None:
                del timeout

            def evaluate(self, script: str, token: str) -> bool:
                assert "https://servicios.froiz.com/api/me" in script
                assert "credentials: 'omit'" in script
                assert token
                return context._token_validation

        return Page()

    def storage_state(self, *, path: str) -> None:
        del path


class _FakeBrowser:
    def __init__(self, response_factory, *, token_validation: bool = False) -> None:
        self._response_factory = response_factory
        self._token_validation = token_validation

    def new_context(self, **kwargs):
        del kwargs
        return _FakeBrowserContext(
            self._response_factory, token_validation=self._token_validation
        )

    def close(self) -> None:
        pass


class _FakePlaywright:
    def __init__(self, response_factory, *, token_validation: bool = False) -> None:
        self.chromium = types.SimpleNamespace(
            launch=lambda **kwargs: _FakeBrowser(
                self._response_factory, token_validation=token_validation
            )
        )
        self._response_factory = response_factory

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    response_factory,
    *,
    token_validation: bool = False,
) -> None:
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: _FakePlaywright(
        response_factory, token_validation=token_validation
    )
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)


def test_addresses_are_redacted_and_calendar_normalizes_slots(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))

    addresses = client.addresses()
    assert addresses == [
        {
            "id": "addr-1",
            "is_default": True,
            "field_names": [
                "id",
                "isDefault",
                "postalCode",
                "street",
            ],
        }
    ]
    serialized = json.dumps(addresses)
    assert "Calle Secreta" not in serialized
    assert "28050" not in serialized

    slots = client.delivery_calendar("28050")
    assert len(slots) == 2
    first, second = slots
    assert (first["date"], first["start"], first["end"]) == (
        "2026-08-22",
        "10:00",
        "12:00",
    )
    assert first["available"] is True and second["available"] is False

    store_call = next(
        r for r in requests if r.url.path.startswith("/api/stores/postalcode/")
    )
    assert store_call.url.path == "/api/stores/postalcode/28050"
    calendar_call = next(
        r for r in requests if r.url.path.startswith("/api/deliverymatrix/calendar/")
    )
    assert calendar_call.url.path == "/api/deliverymatrix/calendar/E1_S2"
    urls = json.dumps([str(r.url) for r in requests])
    assert "/orders" not in urls and "/api/payment" not in urls
    client.close()


def test_channel_cart_id_reads_shop_channel(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    assert client.channel_cart_id() == "cart-uuid"
    me_request = next(r for r in requests if r.url.path == "/api/me")
    assert me_request.headers["authorization"] == "Bearer tok"
    serialized = json.dumps([str(r.url) for r in requests])
    assert "/orders" not in serialized
    assert "/api/payment" not in serialized
    client.close()


def test_raw_read_and_normalize_processed_shape(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    normalized = client.normalize_cart(client.raw_cart("cart-uuid"))
    assert normalized["cart_id"] == "cart-uuid"
    assert normalized["products_count"] == 1
    line = normalized["lines"][0]
    assert line["product_id"] == "p-milk"
    assert line["quantity"] == 1.0
    assert line["unit_price"] == 1.65
    assert normalized["total"] == 1.65
    client.close()


def test_create_update_delete_disposable_round_trip(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    items = [{"product_id": "p-milk", "qty": 1, "unit": "ud", "comment": ""}]
    created = client.create_cart(items)
    assert created["id"] == "new-cart"
    updated = client.update_cart("new-cart", items)
    assert updated["items"][0]["qty"] == 1
    client.delete_cart("new-cart")
    methods_paths = [(r.method, r.url.path) for r in requests]
    assert ("POST", "/api/cart") in methods_paths
    assert ("PUT", "/api/cart/new-cart") in methods_paths
    assert ("DELETE", "/api/cart/new-cart") in methods_paths
    assert all("/orders" not in p for _, p in methods_paths)
    assert all(not p.startswith("/api/payment") for _, p in methods_paths)
    client.close()


def test_stable_version_is_content_derived() -> None:
    base = {"items": [{"product_id": "p1", "qty": 2, "unit": "ud"}], "total": "3.3"}
    same_different_order = {
        "items": [{"unit": "ud", "qty": 2, "product_id": "p1"}],
        "total": "3.30",
    }
    changed = {"items": [{"product_id": "p1", "qty": 3, "unit": "ud"}], "total": "4.95"}
    first = FroizHTTPClient.stable_version(base)
    assert FroizHTTPClient.stable_version(same_different_order) == first
    assert FroizHTTPClient.stable_version(changed) != first


def test_expired_token_refreshes_once_via_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    client = _client(
        tmp_path,
        _router(requests, unauthorized_first=True),
    )
    monkeypatch.setattr(
        client, "_bootstrap_token_via_browser", lambda: "fresh-token", raising=True
    )
    cart_id = client.channel_cart_id()
    assert cart_id == "cart-uuid"
    auth_headers = [
        r.headers.get("authorization") for r in requests if r.url.path == "/api/me"
    ]
    assert auth_headers == ["Bearer tok", "Bearer fresh-token"]
    # The fresh token is persisted for subsequent runs.
    cached = json.loads(client._token_cache_path.read_text(encoding="utf-8"))
    assert cached["token"] == "fresh-token"
    client.close()


def test_processed_read_uses_order_price_and_separates_delivery_total(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    payload = {
        "id": "cart-uuid",
        "items": [
            {
                "enabled": True,
                "product": {
                    "id": "p-water",
                    "name": "Agua",
                    "order_price": 1.77,
                    "base_price": "1.77",
                },
                "qty": 1,
                "unit": "ud",
                "comment": "",
            }
        ],
        "subtotal": 1.77,
        "total": 5.77,
    }
    client = _client(tmp_path, _router(requests, raw_payload=payload))

    normalized = client.normalize_cart(client.processed_cart("cart-uuid"))

    assert normalized["lines"][0]["unit_price"] == 1.77
    assert normalized["lines"][0]["metadata"]["price_source"] == (
        "authenticated.cart.order_price"
    )
    assert normalized["subtotal"] == 1.77
    assert normalized["total"] == 5.77
    assert ("GET", "/api/cart/cart-uuid") in [
        (request.method, request.url.path) for request in requests
    ]
    client.close()


def test_stable_version_changes_when_optional_units_change() -> None:
    first = {"id": "cart-uuid", "items": [{"product_id": "p", "qty": 1, "unit": "ud", "units": 1}]}
    second = {"id": "cart-uuid", "items": [{"product_id": "p", "qty": 1, "unit": "ud", "units": 2}]}

    assert FroizHTTPClient.stable_version(first) != FroizHTTPClient.stable_version(second)


def test_authenticated_product_search_sends_location_context(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))

    products = client.search_products(
        "agua mineral 1 l", store="E1_S2", page=1, size=20
    )

    assert products[0]["id"] == "p-water"
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/products")
    ]
    client.close()


def test_catalogue_search_never_bootstraps_browser_on_unauthorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        tmp_path,
        httpx.MockTransport(lambda _: httpx.Response(401)),
    )

    def unexpected_refresh() -> None:
        raise AssertionError("catalogue search must not launch browser token refresh")

    monkeypatch.setattr(client, "_refresh_token", unexpected_refresh)

    with pytest.raises(AuthenticationRequired):
        client.search_products(
            "leche",
            store="E1_S2",
            allow_browser_refresh=False,
        )
    client.close()


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (200, {"authenticated": False, "userChannelOptions": []}),
        (401, {"authenticated": False}),
    ],
)
def test_bootstrap_rejects_guest_or_unauthorized_me_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    payload: object,
) -> None:
    state_path = _state(tmp_path)

    def response_factory():
        request = _FakeBrowserRequest(
            "https://servicios.froiz.com/api/me", token="candidate"
        )
        return request, _FakeBrowserResponse(request, status, payload)

    _install_fake_playwright(monkeypatch, response_factory)
    client = FroizHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=_router([])),
        token_cache_path=tmp_path / "missing-cache.json",
    )
    assert client._bootstrap_token_via_browser() is None
    client.close()


def test_bootstrap_accepts_only_valid_authenticated_me_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = _state(tmp_path)

    def response_factory():
        request = _FakeBrowserRequest(
            "https://servicios.froiz.com/api/me", token="verified"
        )
        return request, _FakeBrowserResponse(
            request,
            200,
            {"id": "user-1", "userChannelOptions": []},
        )

    _install_fake_playwright(monkeypatch, response_factory)
    client = FroizHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=_router([])),
        token_cache_path=tmp_path / "missing-cache.json",
    )
    assert client._bootstrap_token_via_browser() == "verified"
    client.close()


def test_bootstrap_ignores_bearer_from_non_me_api_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = _state(tmp_path)

    def response_factory():
        request = _FakeBrowserRequest(
            "https://servicios.froiz.com/api/cart", token="candidate"
        )
        return request, _FakeBrowserResponse(
            request,
            200,
            {"id": "user-1", "userChannelOptions": []},
        )

    _install_fake_playwright(monkeypatch, response_factory)
    client = FroizHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=_router([])),
        token_cache_path=tmp_path / "missing-cache.json",
    )
    assert client._bootstrap_token_via_browser() is None
    client.close()


def test_bootstrap_validates_candidate_from_other_read_only_api_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = _state(tmp_path)

    def response_factory():
        request = _FakeBrowserRequest(
            "https://servicios.froiz.com/api/config", token="candidate"
        )
        return request, _FakeBrowserResponse(request, 200, {"public": True})

    _install_fake_playwright(
        monkeypatch, response_factory, token_validation=True
    )
    client = FroizHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=_router([])),
        token_cache_path=tmp_path / "missing-cache.json",
    )

    assert client._bootstrap_token_via_browser() == "candidate"
    client.close()


def test_storage_token_candidates_use_only_exact_keys_and_origin() -> None:
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
    assert FroizHTTPClient._storage_token_candidates(trusted) == ("candidate",)
    external = Page("https://evil.example")
    assert FroizHTTPClient._storage_token_candidates(external) == ()
    assert external.calls == 1


def test_token_validation_rejects_an_external_page() -> None:
    class Page:
        url = "https://accounts.example.test/login"

        def evaluate(self, *_args):
            raise AssertionError("external page must not receive the bearer")

    assert FroizHTTPClient._token_authenticated_in_page(Page(), "candidate") is False


def test_missing_session_without_bootstrap_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(
        tmp_path,
        _router(requests, unauthorized_first=True),
    )
    monkeypatch.setattr(client, "_bootstrap_token_via_browser", lambda: None, raising=True)
    with pytest.raises(AuthenticationRequired):
        client.channel_cart_id()
    client.close()


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_unauthorized_froiz_mutation_is_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401)

    client = _client(tmp_path, httpx.MockTransport(handler))
    monkeypatch.setattr(
        client, "_bootstrap_token_via_browser", lambda: "fresh", raising=True
    )
    with pytest.raises(AuthenticationRequired, match="not retried"):
        client._request(method, "/api/cart/cart-1", json_body={"items": []})
    assert len(requests) == 1
    client.close()


def test_froiz_token_cache_is_bound_to_storage_state(tmp_path: Path) -> None:
    client = _client(tmp_path, _router([]))
    assert client._stored_token() == "tok"
    client.state_path.write_text(
        json.dumps({"cookies": [], "origins": [{"origin": "changed"}]}),
        encoding="utf-8",
    )
    assert client._stored_token() is None
    client.close()


def test_froiz_cookie_token_rejects_lookalike_domain(tmp_path: Path) -> None:
    state_path = _state(tmp_path)
    state_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "auth._token.froiz",
                        "value": "Bearer private",
                        "domain": "supermercado.froiz.com.evil.test",
                        "path": "/",
                        "expires": -1,
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    client = FroizHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=_router([])),
        token_cache_path=tmp_path / "missing-cache.json",
    )
    assert client._cookie_token() is None
    client.close()
