from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest

from open_grocery_mcp.errors import BudgetExceeded, ConcurrentCartChange
from open_grocery_mcp.providers.mercadona_account import MercadonaAccountClient
from tests.mercadona_helpers import cart_payload, jwt, write_state

def test_cart_commit_checks_version_and_writes_once(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    writes: list[dict] = []
    get_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            get_count += 1
            if get_count <= 2:
                return httpx.Response(200, json=cart_payload())
            return httpx.Response(200, json=cart_payload(version=8, total='2.00', product_id='20', name='Arroz', quantity=1, unit_price='2.00'))
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(200, json={'price_instructions': {'unit_price': '2.00'}})
        if request.url.path.endswith('/cart/') and request.method == 'PUT':
            writes.append(json.loads(request.content))
            return httpx.Response(200, json={})
        raise AssertionError(request.url)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_cart_update([{'product_id': '20', 'quantity': 1}], mode='replace', expected_version=7, max_total=Decimal('5'))
    result = client.commit_cart_update(plan)
    assert result['retailer_cart_modified'] is True
    assert writes[0]['lines'] == [{'product_id': '20', 'quantity': 1.0, 'sources': []}]
    http.close()

def test_cart_commit_refuses_stale_review(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=cart_payload(version=9))
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    with pytest.raises(ConcurrentCartChange):
        client.preview_cart_update([], mode='replace', expected_version=7, max_total=Decimal('5'))
    http.close()

def test_cart_commit_restores_previous_lines_when_remote_total_exceeds_cap(tmp_path) -> None:
    state_path = tmp_path / 'state.json'
    write_state(state_path, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    remote = 'old'
    writes: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal remote
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            if remote == 'old':
                return httpx.Response(200, json=cart_payload())
            return httpx.Response(200, json=cart_payload(version=8, total='7.00', product_id='20', name='Arroz', quantity=1, unit_price='7.00'))
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(200, json={'price_instructions': {'unit_price': '2.00'}})
        if request.url.path.endswith('/cart/') and request.method == 'PUT':
            body = json.loads(request.content)
            writes.append(body)
            remote = 'old' if body['lines'][0]['product_id'] == '10' else 'new'
            return httpx.Response(200, json={})
        raise AssertionError(request.url)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state_path, client=http)
    plan = client.preview_cart_update([{'product_id': '20', 'quantity': 1}], mode='replace', expected_version=7, max_total=Decimal('5'))
    with pytest.raises(BudgetExceeded, match='restored'):
        client.commit_cart_update(plan)
    assert len(writes) == 2
    assert writes[-1]['lines'][0]['product_id'] == '10'
    http.close()
