from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest

from open_grocery_mcp.errors import AuthenticationRequired, BudgetExceeded
from open_grocery_mcp.providers.mercadona_account import MercadonaAccountClient
from open_grocery_mcp.providers.mercadona_state import (
    MercadonaStateClient,
    _default_state_path,
)
from tests.mercadona_helpers import cart_payload, jwt, write_state


def test_default_state_path_honors_shared_state_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv('OPEN_GROCERY_MERCADONA_STATE_PATH', raising=False)
    monkeypatch.setenv('OPEN_GROCERY_STATE_DIR', str(tmp_path))

    assert _default_state_path() == tmp_path / 'mercadona' / 'storage_state.json'

def test_status_and_cart_preview_are_budget_guarded(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            return httpx.Response(200, headers={'x-customer-wh': 'mad1'}, json=cart_payload())
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(200, json={'id': '20', 'display_name': 'Arroz', 'price_instructions': {'unit_price': '2.00'}})
        raise AssertionError(request.url)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    status = client.status()
    assert status['authenticated'] is True
    assert status['validated_live'] is False
    plan = client.preview_cart_update([{'product_id': '20', 'quantity': 2}], mode='merge', expected_version=7, max_total=Decimal('10'))
    assert plan['estimated_total_text'] == '7.00'
    with pytest.raises(BudgetExceeded):
        client.preview_cart_update([{'product_id': '20', 'quantity': 2}], mode='merge', expected_version=7, max_total=Decimal('6.99'))
    http.close()

def test_refresh_updates_storage_state(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) - 10, 'customer_uuid': 'customer-1'}))
    fresh = jwt({'exp': int(time.time()) + 7200, 'customer_uuid': 'customer-1'})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/auth/tokens/':
            return httpx.Response(200, json={'access_token': fresh, 'refresh_token': 'refresh-2'})
        if request.url.path.endswith('/cart/'):
            return httpx.Response(200, json=cart_payload())
        raise AssertionError(request.url)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    assert client.cart()['cart_id'] == 'cart-1'
    stored = json.loads(state.read_text())
    user = json.loads(stored['origins'][0]['localStorage'][0]['value'])
    assert user['token'] == fresh
    assert user['refreshToken'] == 'refresh-2'
    http.close()


def test_mutating_401_is_never_retried(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    with pytest.raises(AuthenticationRequired, match='not retried'):
        client._request('POST', '/unsafe-write/', json_body={'safe': False})
    assert calls == 1
    http.close()


def test_status_rejects_lookalike_mercadona_origin(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    payload = json.loads(state.read_text(encoding='utf-8'))
    payload['origins'][0]['origin'] = 'https://evilmercadona.es'
    state.write_text(json.dumps(payload), encoding='utf-8')
    client = MercadonaAccountClient(
        state_path=state,
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )

    status = client.status()

    assert status['authenticated'] is False
    assert status['validated_live'] is False
    assert 'customer_id_suffix' not in status
    client.close()


@pytest.mark.parametrize(
    'origin',
    [
        'https://account.mercadona.es',
        'http://tienda.mercadona.es',
    ],
)
def test_status_accepts_only_the_exact_https_storefront_origin(
    tmp_path, origin
) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    payload = json.loads(state.read_text(encoding='utf-8'))
    payload['origins'][0]['origin'] = origin
    state.write_text(json.dumps(payload), encoding='utf-8')
    client = MercadonaAccountClient(
        state_path=state,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ),
    )

    assert client.status()['authenticated'] is False
    client.close()


def test_cookie_header_filters_domain_path_and_expiration() -> None:
    now = time.time()
    state = {
        'cookies': [
            {'name': 'root', 'value': 'ok', 'domain': '.mercadona.es', 'path': '/', 'expires': -1},
            {'name': 'api', 'value': 'ok', 'domain': 'tienda.mercadona.es', 'path': '/api', 'expires': now + 60},
            {'name': 'wrong-host', 'value': 'no', 'domain': 'evil.mercadona.es', 'path': '/', 'expires': -1},
            {'name': 'wrong-path', 'value': 'no', 'domain': 'mercadona.es', 'path': '/checkout', 'expires': -1},
            {'name': 'expired', 'value': 'no', 'domain': 'mercadona.es', 'path': '/', 'expires': now - 1},
            {'name': 'bad-expiry', 'value': 'no', 'domain': 'mercadona.es', 'path': '/', 'expires': 'not-a-time'},
        ]
    }

    header = MercadonaStateClient._cookie_header(state)

    assert header == 'root=ok; api=ok'
    assert 'wrong-host' not in header
    assert 'expired' not in header
    assert 'not-a-time' not in header
