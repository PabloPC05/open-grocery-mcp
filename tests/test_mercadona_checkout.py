from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    OrderSubmissionDisabled,
    ProviderError,
)
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
    monkeypatch.setenv('OPEN_GROCERY_ENABLE_RETAILER_WRITES', '1')
    monkeypatch.setenv('OPEN_GROCERY_ENABLE_ORDER_SUBMISSION', '1')
    state_path = tmp_path / 'state.json'
    write_state(state_path, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    calls: list[tuple[str, str, dict | None]] = []
    delivery_selected = False
    address = {
        'id': '1',
        'alias': 'Casa',
        'address': 'private street held in memory',
        'address_detail': 'private detail held in memory',
        'postal_code': '15001',
        'town': 'A Coruña',
        'unexpected': 'must-not-be-sent',
    }
    slot = {
        'id': 'slot-1',
        'available': True,
        'open': True,
        'price': '1.50',
        'start': '2026-08-21T10:00:00Z',
        'cutoff_time': '2026-08-21T08:00:00Z',
        'timezone': 'Europe/Madrid',
        'unexpected': 'must-not-be-sent',
    }
    authoritative_cart = cart_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith('/cart/'):
            return httpx.Response(200, json=cart_payload())
        if request.url.path.endswith('/checkouts/') and request.method == 'POST':
            assert body['cart']['id'] == 'cart-1'
            return httpx.Response(200, json={'id': 'check-1', 'summary': {'total': '3.00'}})
        if request.url.path.endswith('/customers/customer-1/addresses/'):
            return httpx.Response(200, json={'results': [address]})
        if request.url.path.endswith('/addresses/1/slots/'):
            return httpx.Response(200, json={'results': [slot]})
        if request.url.path.endswith('/delivery-info/') and request.method == 'PUT':
            nonlocal delivery_selected
            assert body == {
                'address': {
                    'id': '1',
                    'address': 'private street held in memory',
                    'address_detail': 'private detail held in memory',
                    'postal_code': '15001',
                    'town': 'A Coruña',
                },
                'slot': {
                    'id': 'slot-1',
                    'available': True,
                    'open': True,
                    'price': '1.50',
                    'start': '2026-08-21T10:00:00Z',
                    'cutoff_time': '2026-08-21T08:00:00Z',
                    'timezone': 'Europe/Madrid',
                },
            }
            delivery_selected = True
            return httpx.Response(200, json={'id': 'check-1', 'summary': {'total': '4.50'}, 'address': {'id': '1'}, 'slot': {'id': 'slot-1', 'start': '2026-08-21T10:00:00Z'}})
        if request.url.path.endswith('/checkouts/check-1/') and request.method == 'GET':
            return httpx.Response(200, json={'id': 'check-1', 'summary': {'total': '4.50' if delivery_selected else '3.00'}, 'cart': authoritative_cart, 'address': {'id': '1'} if delivery_selected else {}, 'slot': {'id': 'slot-1'} if delivery_selected else {}})
        if request.url.path.endswith('/checkouts/check-1/confirm/'):
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


def test_checkout_creation_rejects_same_version_with_changed_lines(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    changed = False
    checkout_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal checkout_posts
        if request.url.path.endswith('/cart/'):
            if changed:
                return httpx.Response(
                    200,
                    json=cart_payload(
                        version=7,
                        total='3.00',
                        product_id='other',
                        quantity=1,
                        unit_price='3.00',
                    ),
                )
            return httpx.Response(200, json=cart_payload())
        if request.url.path.endswith('/checkouts/'):
            checkout_posts += 1
            return httpx.Response(200, json={'id': 'unexpected'})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_checkout(expected_version=7, max_total=Decimal('5'))
    changed = True
    with pytest.raises(ConcurrentCartChange, match='cart lines changed'):
        client.create_checkout(plan)
    assert checkout_posts == 0
    http.close()


def test_delivery_fee_is_checked_before_first_selection_write(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    delivery_puts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal delivery_puts
        if request.url.path.endswith('/customers/customer-1/addresses/'):
            return httpx.Response(200, json={'results': [{'id': '1', 'alias': 'Casa'}]})
        if request.url.path.endswith('/addresses/1/slots/'):
            return httpx.Response(
                200,
                json={
                    'results': [
                        {
                            'id': 'slot-1',
                            'available': True,
                            'open': True,
                            'price': '2.00',
                        }
                    ]
                },
            )
        if request.url.path.endswith('/checkouts/check-1/'):
            return httpx.Response(
                200, json={'id': 'check-1', 'summary': {'total': '4.00'}}
            )
        if request.url.path.endswith('/delivery-info/'):
            delivery_puts += 1
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    with pytest.raises(BudgetExceeded, match='delivery fee exceeds'):
        client.set_checkout_delivery(
            'check-1', address_id='1', slot_id='slot-1', max_total=Decimal('5')
        )
    assert delivery_puts == 0
    http.close()


def test_slots_without_explicit_availability_fail_closed(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'results': [{'id': 'slot-1'}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    assert client.slots('1') == [
        {
            'id': 'slot-1',
            'start': None,
            'end': None,
            'price': 0.0,
            'price_text': '0.00',
            'available': False,
            'open': False,
            'cutoff_time': None,
            'timezone': None,
        }
    ]
    http.close()


def test_addresses_and_slots_send_localized_warehouse_context_and_size(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith('/addresses/'):
            return httpx.Response(200, json={'results': [{'id': 'a1', 'alias': 'Casa'}]}, headers={'x-customer-wh': 'wh-42'})
        if request.url.path.endswith('/addresses/a1/slots/'):
            return httpx.Response(200, json={'results': []})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    assert client.addresses()[0]['id'] == 'a1'
    client.slots('a1')
    assert seen[0].url.params['lang'] == 'es'
    assert seen[1].url.params['lang'] == 'es'
    assert seen[1].url.params['wh'] == 'wh-42'
    assert seen[1].url.params['size'] == '100'
    http.close()


def test_checkout_creation_401_is_not_retried(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.url.path.endswith('/cart/'):
            return httpx.Response(200, json=cart_payload())
        if request.url.path.endswith('/checkouts/'):
            posts += 1
            return httpx.Response(401)
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_checkout(expected_version=7, max_total=Decimal('5'))
    with pytest.raises(AuthenticationRequired):
        client.create_checkout(plan)
    assert posts == 1
    http.close()


def test_checkout_creation_failure_reports_status_without_private_id(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'private-customer'}))
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.url.path.endswith('/cart/'):
            return httpx.Response(200, json=cart_payload())
        if request.url.path.endswith('/checkouts/'):
            posts += 1
            return httpx.Response(422)
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_checkout(expected_version=7, max_total=Decimal('5'))
    with pytest.raises(ProviderError, match=r'ambiguous \(HTTP 422\)') as raised:
        client.create_checkout(plan)
    assert posts == 1
    assert raised.value.status_code == 422
    assert raised.value.operation == 'checkout_create'
    assert 'private-customer' not in str(raised.value)
    http.close()


def test_request_errors_redact_private_route_ids(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'private-customer'}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    with pytest.raises(ProviderError) as raised:
        client.get_checkout('private-checkout')
    message = str(raised.value)
    assert 'private-customer' not in message
    assert 'private-checkout' not in message
    assert '/customers/<private>/checkouts/<private>/' in message
    assert raised.value.status_code == 500
    http.close()


def test_delivery_timeout_is_ambiguous_without_retry(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    puts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal puts
        if request.url.path.endswith('/customers/customer-1/addresses/'):
            return httpx.Response(200, json={'results': [{'id': 'a1', 'alias': 'Casa'}]})
        if request.url.path.endswith('/addresses/a1/slots/'):
            return httpx.Response(200, json={'results': [{'id': 's1', 'available': True, 'open': True, 'price': '1.00'}]})
        if request.url.path.endswith('/checkouts/c1/'):
            return httpx.Response(200, json={'id': 'c1', 'summary': {'total': '3.00'}, 'address': {}, 'slot': {}})
        if request.url.path.endswith('/delivery-info/'):
            puts += 1
            raise httpx.ReadTimeout('simulated timeout')
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    with pytest.raises(ProviderError, match='did not reach|ambiguous'):
        client.set_checkout_delivery('c1', address_id='a1', slot_id='s1', max_total=Decimal('5'))
    assert puts == 1
    http.close()


def test_delivery_over_cap_from_empty_checkout_reports_no_rollback(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    puts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/customers/customer-1/addresses/'):
            return httpx.Response(200, json={'results': [{'id': 'a1', 'alias': 'Casa'}]})
        if request.url.path.endswith('/addresses/a1/slots/'):
            return httpx.Response(200, json={'results': [{'id': 's1', 'available': True, 'open': True, 'price': '1.00'}]})
        if request.url.path.endswith('/checkouts/c1/'):
            selected = bool(puts)
            return httpx.Response(200, json={'id': 'c1', 'summary': {'total': '6.00' if selected else '3.00'}, 'address': {'id': 'a1'} if selected else {}, 'slot': {'id': 's1'} if selected else {}})
        if request.url.path.endswith('/delivery-info/'):
            puts.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 'c1'})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    with pytest.raises(ProviderError, match='no safe delivery-clear operation'):
        client.set_checkout_delivery('c1', address_id='a1', slot_id='s1', max_total=Decimal('5'))
    assert len(puts) == 1
    http.close()


def test_order_result_without_id_is_unverified_and_cannot_be_retried(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv('OPEN_GROCERY_ENABLE_RETAILER_WRITES', '1')
    monkeypatch.setenv('OPEN_GROCERY_ENABLE_ORDER_SUBMISSION', '1')
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.url.path.endswith('/checkouts/check-1/'):
            return httpx.Response(
                200,
                json={
                    'id': 'check-1',
                    'summary': {'total': '4.50'},
                    'address': {'id': '1'},
                    'slot': {'id': 'slot-1'},
                },
            )
        if request.url.path.endswith('/checkouts/check-1/confirm/'):
            posts += 1
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    with pytest.raises(ProviderError, match='no verifiable order id'):
        client.submit_order('check-1', max_total=Decimal('5'))
    with pytest.raises(InvalidRequest, match='already attempted'):
        client.submit_order('check-1', max_total=Decimal('5'))
    assert posts == 1
    http.close()
