from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest

from open_grocery_mcp.errors import BudgetExceeded
from open_grocery_mcp.providers.mercadona_account import MercadonaAccountClient
from tests.mercadona_helpers import cart_payload, jwt, write_state

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
    assert client.status()['authenticated'] is True
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
