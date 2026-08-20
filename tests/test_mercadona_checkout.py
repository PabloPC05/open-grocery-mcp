from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest

from open_grocery_mcp.errors import OrderSubmissionDisabled
from open_grocery_mcp.providers.mercadona_account import MercadonaAccountClient
from tests.mercadona_helpers import cart_payload, jwt, write_state

def test_order_submission_is_disabled_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv('OPEN_GROCERY_ENABLE_ORDER_SUBMISSION', raising=False)
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/checkouts/check-1/'):
            return httpx.Response(200, json={'id': 'check-1', 'summary': {'total': '9.50'}, 'address': {'id': 1}, 'slot': {'id': 'slot-1'}})
        raise AssertionError(request.url)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    with pytest.raises(OrderSubmissionDisabled):
        client.submit_order('check-1', max_total=Decimal('10'))
    http.close()

def test_checkout_creation_delivery_and_order_endpoint_shapes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('OPEN_GROCERY_ENABLE_ORDER_SUBMISSION', '1')
    state_path = tmp_path / 'state.json'
    write_state(state_path, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    calls: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith('/cart/'):
            return httpx.Response(200, json=cart_payload())
        if request.url.path.endswith('/checkouts/') and request.method == 'POST':
            assert body['cart']['id'] == 'cart-1'
            return httpx.Response(200, json={'id': 'check-1', 'summary': {'total': '3.00'}})
        if request.url.path.endswith('/delivery-info/') and request.method == 'PUT':
            assert body == {'address': {'id': '1'}, 'slot': {'id': 'slot-1'}}
            return httpx.Response(200, json={'id': 'check-1', 'summary': {'total': '4.50'}, 'address': {'id': '1'}, 'slot': {'id': 'slot-1', 'start': '2026-08-21T10:00:00Z'}})
        if request.url.path.endswith('/checkouts/check-1/') and request.method == 'GET':
            return httpx.Response(200, json={'id': 'check-1', 'summary': {'total': '4.50'}, 'address': {'id': '1'}, 'slot': {'id': 'slot-1'}})
        if request.url.path.endswith('/checkouts/check-1/orders/'):
            return httpx.Response(200, json={'order_id': 'order-1'})
        raise AssertionError(request.url)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state_path, client=http)
    plan = client.preview_checkout(expected_version=7, max_total=Decimal('5'))
    checkout = client.create_checkout(plan)
    assert checkout['checkout_created'] is True
    delivery = client.set_checkout_delivery('check-1', address_id='1', slot_id='slot-1', max_total=Decimal('5'))
    assert delivery['total_text'] == '4.50'
    order = client.submit_order('check-1', max_total=Decimal('5'))
    assert order['order_placed'] is True
    assert order['order_id'] == 'order-1'
    http.close()
