from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest

from open_grocery_mcp.errors import (
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    ProviderError,
)
from open_grocery_mcp.providers.mercadona_account import MercadonaAccountClient
from open_grocery_mcp.providers.mercadona_cart_commit import MercadonaCartCommitMixin
from tests.mercadona_helpers import cart_payload, jwt, write_state


def _cart_with_line_metadata(
    *,
    version: int,
    quantity: float,
    total: str,
    line_version: int,
    sources: list[str] | None = None,
) -> dict:
    return {
        'id': 'cart-1',
        'version': version,
        'products_count': 1,
        'summary': {'total': total},
        'lines': [
            {
                'id': 'line-1',
                'version': line_version,
                'quantity': quantity,
                'sources': sources if sources is not None else ['+source-1'],
                'product': {
                    'id': '10',
                    'display_name': 'Leche',
                    'price_instructions': {'unit_price': '1.50'},
                },
            }
        ],
    }


def test_cart_read_preserves_cart_and_line_metadata(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_cart_with_line_metadata(
                    version=7, quantity=2, total='3.00', line_version=4
                ),
            )
        )
    )
    client = MercadonaAccountClient(state_path=state, client=http)

    cart = client.cart()

    assert cart['cart_id'] == 'cart-1'
    assert cart['version'] == 7
    assert cart['lines'][0]['id'] == 'line-1'
    assert cart['lines'][0]['version'] == 4
    assert cart['lines'][0]['sources'] == ['+source-1']
    http.close()


def test_line_signature_keeps_source_operation_shape_but_ignores_opaque_labels() -> None:
    expected = [
        {
            'product_id': '10',
            'quantity': 1,
            'sources': ['+CA', '-CA'],
        }
    ]
    retailer_labels = [
        {
            'product_id': '10',
            'quantity': 1,
            'sources': ['+source-1', '-source-2'],
        }
    ]
    different_operations = [
        {
            'product_id': '10',
            'quantity': 1,
            'sources': ['+source-1', '+source-2'],
        }
    ]

    assert MercadonaCartCommitMixin._source_history_matches(
        retailer_labels, expected
    )
    assert not MercadonaCartCommitMixin._source_history_matches(
        different_operations, expected
    )


@pytest.mark.parametrize(
    'payload',
    [
        {'id': 'cart-1', 'version': 7},
        {'id': 'cart-1', 'version': -1, 'lines': []},
        {'id': 'cart-1', 'version': 'unknown', 'lines': []},
        {'version': 7, 'lines': []},
    ],
)
def test_ambiguous_cart_read_fails_closed(tmp_path, payload) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    client = MercadonaAccountClient(state_path=state, client=http)

    with pytest.raises(ProviderError, match='cart response'):
        client.cart()

    http.close()


@pytest.mark.parametrize(
    ('field', 'value'),
    [('id', {}), ('version', []), ('sources', [{'code': 'source-1'}])],
)
def test_cart_read_rejects_nonprimitive_line_metadata(tmp_path, field, value) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    payload = _cart_with_line_metadata(
        version=7, quantity=2, total='3.00', line_version=4
    )
    payload['lines'][0][field] = value
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    client = MercadonaAccountClient(state_path=state, client=http)

    with pytest.raises(ProviderError, match='line'):
        client.cart()

    http.close()


@pytest.mark.parametrize(
    'line_update',
    [
        {'quantity': 'not-a-number'},
        {'quantity': 0},
        {'product': {'display_name': 'Leche'}},
    ],
)
def test_cart_read_rejects_malformed_line_identity_or_quantity(tmp_path, line_update) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'})
    )
    payload = _cart_with_line_metadata(
        version=7, quantity=2, total='3.00', line_version=4
    )
    payload['lines'][0].update(line_update)
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    client = MercadonaAccountClient(state_path=state, client=http)

    with pytest.raises(ProviderError, match='line'):
        client.cart()

    http.close()


def test_quantity_change_appends_programmatic_source_operation(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    payload = _cart_with_line_metadata(
        version=7, quantity=2, total='3.00', line_version=4
    )
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    client = MercadonaAccountClient(state_path=state, client=http)

    plan = client.preview_cart_update(
        [{'product_id': '10', 'quantity': 1}],
        mode='merge',
        expected_version=7,
        max_total=Decimal('5'),
    )

    assert plan['desired_lines'][0]['sources'] == ['+source-1', '-CA']

    http.close()


@pytest.mark.parametrize(
    ('quantity', 'expected_suffix'),
    [
        (4, ['+CA', '+CA']),
        (0.5, ['-CA', '-CA']),
    ],
)
def test_quantity_delta_has_one_source_operation_per_unit(tmp_path, quantity, expected_suffix) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    payload = _cart_with_line_metadata(
        version=7, quantity=2, total='3.00', line_version=4
    )
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    client = MercadonaAccountClient(state_path=state, client=http)

    plan = client.preview_cart_update(
        [{'product_id': '10', 'quantity': quantity}],
        mode='merge',
        expected_version=7,
        max_total=Decimal('10'),
    )

    assert plan['desired_lines'][0]['sources'] == ['+source-1', *expected_suffix]
    http.close()


def test_new_multi_unit_line_repeats_programmatic_source_operation(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(
                200,
                json={'id': '20', 'price_instructions': {'unit_price': '2.00'}},
            )
        return httpx.Response(200, json=cart_payload())

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)

    plan = client.preview_cart_update(
        [{'product_id': '20', 'quantity': 3}],
        mode='replace',
        expected_version=7,
        max_total=Decimal('10'),
    )

    assert plan['desired_lines'][0]['sources'] == ['+CA', '+CA', '+CA']
    http.close()


def test_cart_put_preserves_existing_line_metadata_and_cart_version(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    writes: list[dict] = []
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            reads += 1
            if reads < 3:
                return httpx.Response(
                    200,
                    json=_cart_with_line_metadata(
                        version=7,
                        quantity=2,
                        total='3.00',
                        line_version=4,
                        sources=['+source-1'],
                    ),
                )
            return httpx.Response(
                200,
                json=_cart_with_line_metadata(
                    version=8,
                    quantity=1,
                    total='1.50',
                    line_version=5,
                    sources=['+source-1', '-source-1'],
                ),
            )
        if request.url.path.endswith('/cart/') and request.method == 'PUT':
            writes.append(json.loads(request.content))
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_cart_update(
        [{'product_id': '10', 'quantity': 1}],
        mode='merge',
        expected_version=7,
        max_total=Decimal('5'),
    )

    result = client.commit_cart_update(plan)

    assert result['version'] == 8
    assert writes == [
        {
            'id': 'cart-1',
            'version': 7,
            'lines': [
                {
                    'product_id': '10',
                    'quantity': 1.0,
                    'sources': ['+source-1', '-CA'],
                    'id': 'line-1',
                    'version': 4,
                }
            ],
        }
    ]
    http.close()


def test_cart_commit_refuses_changed_version_before_put(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    reads = 0
    writes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads, writes
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            reads += 1
            return httpx.Response(
                200,
                json=cart_payload(version=7 if reads == 1 else 8),
            )
        if request.url.path.endswith('/cart/') and request.method == 'PUT':
            writes += 1
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_cart_update(
        [{'product_id': '10', 'quantity': 1}],
        mode='replace',
        expected_version=7,
        max_total=Decimal('5'),
    )

    with pytest.raises(ConcurrentCartChange, match='changed from version 7 to 8'):
        client.commit_cart_update(plan)

    assert writes == 0
    http.close()

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
    assert writes[0]['lines'] == [
        {'product_id': '20', 'quantity': 1.0, 'sources': ['+CA']}
    ]
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


def test_cart_commit_rejects_a_tampered_plan_before_writing(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    writes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal writes
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            return httpx.Response(200, json=cart_payload())
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(
                200, json={'price_instructions': {'unit_price': '2.00'}}
            )
        if request.method == 'PUT':
            writes += 1
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_cart_update(
        [{'product_id': '20', 'quantity': 1}],
        mode='replace',
        expected_version=7,
        max_total=Decimal('5'),
    )
    plan['desired_lines'][0]['quantity'] = -1

    with pytest.raises(InvalidRequest, match='invalid'):
        client.commit_cart_update(plan)

    assert writes == 0
    http.close()


@pytest.mark.parametrize('field', ['id', 'version', 'sources'])
def test_cart_commit_rejects_tampered_line_metadata_before_writing(tmp_path, field) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    writes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal writes
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            return httpx.Response(200, json=cart_payload())
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(
                200, json={'price_instructions': {'unit_price': '2.00'}}
            )
        if request.method == 'PUT':
            writes += 1
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_cart_update(
        [{'product_id': '20', 'quantity': 1}],
        mode='replace',
        expected_version=7,
        max_total=Decimal('5'),
    )
    plan['desired_lines'][0][field] = {} if field != 'sources' else [{'code': 'CA'}]

    with pytest.raises(InvalidRequest, match='metadata'):
        client.commit_cart_update(plan)

    assert writes == 0
    http.close()


def test_cart_commit_restores_price_change_below_cap(tmp_path) -> None:
    state_path = tmp_path / 'state.json'
    write_state(
        state_path,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    remote = 'old'

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal remote
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            if remote == 'old':
                return httpx.Response(200, json=cart_payload())
            return httpx.Response(
                200,
                json=cart_payload(
                    version=8,
                    total='2.50',
                    product_id='20',
                    name='Arroz',
                    quantity=1,
                    unit_price='2.50',
                ),
            )
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(
                200,
                json={
                    'id': '20',
                    'display_name': 'Arroz',
                    'price_instructions': {'unit_price': '2.00'},
                },
            )
        if request.url.path.endswith('/cart/') and request.method == 'PUT':
            body = json.loads(request.content)
            remote = 'old' if body['lines'][0]['product_id'] == '10' else 'new'
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state_path, client=http)
    plan = client.preview_cart_update(
        [{'product_id': '20', 'quantity': 1}],
        mode='replace',
        expected_version=7,
        max_total=Decimal('5'),
    )
    with pytest.raises(ProviderError, match='previous cart restored'):
        client.commit_cart_update(plan)
    assert remote == 'old'
    http.close()


@pytest.mark.parametrize('quantity', [-1, 'invalid', True, 1001])
def test_cart_preview_rejects_unsafe_quantities(tmp_path, quantity) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=cart_payload())
        )
    )
    client = MercadonaAccountClient(state_path=state, client=http)

    with pytest.raises(InvalidRequest):
        client.preview_cart_update(
            [{'product_id': '20', 'quantity': quantity}],
            mode='replace',
            expected_version=7,
            max_total=Decimal('10'),
        )
    http.close()


def test_cart_preview_rejects_restricted_products_before_writing(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=cart_payload())
        )
    )
    client = MercadonaAccountClient(state_path=state, client=http)

    with pytest.raises(InvalidRequest, match='age-restricted'):
        client.preview_cart_update(
            [{'product_id': '20', 'name': 'Vino tinto', 'quantity': 1}],
            mode='replace',
            expected_version=7,
            max_total=Decimal('10'),
        )
    http.close()


def test_ambiguous_cart_write_is_not_retried_when_state_is_verified(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(
        state,
        token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}),
    )
    remote = 'old'
    put_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal remote, put_count
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(
                200,
                json={
                    'id': '20',
                    'display_name': 'Arroz',
                    'price_instructions': {'unit_price': '2.00'},
                },
            )
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            if remote == 'old':
                return httpx.Response(200, json=cart_payload())
            return httpx.Response(
                200,
                json=cart_payload(
                    version=8,
                    total='2.00',
                    product_id='20',
                    name='Arroz',
                    quantity=1,
                    unit_price='2.00',
                ),
            )
        if request.url.path.endswith('/cart/') and request.method == 'PUT':
            put_count += 1
            remote = 'new'
            return httpx.Response(401)
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_cart_update(
        [{'product_id': '20', 'quantity': 1}],
        mode='replace',
        expected_version=7,
        max_total=Decimal('5'),
    )

    result = client.commit_cart_update(plan)

    assert put_count == 1
    assert result['write_response_ambiguous_but_state_verified'] is True
    http.close()


def test_ambiguous_cart_write_does_not_restore_unrecognized_concurrent_state(tmp_path) -> None:
    state = tmp_path / 'state.json'
    write_state(state, token=jwt({'exp': int(time.time()) + 3600, 'customer_uuid': 'customer-1'}))
    remote = 'old'
    put_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal remote, put_count
        if request.url.path.endswith('/products/20/'):
            return httpx.Response(
                200,
                json={
                    'id': '20',
                    'display_name': 'Arroz',
                    'price_instructions': {'unit_price': '2.00'},
                },
            )
        if request.url.path.endswith('/cart/') and request.method == 'GET':
            if remote == 'old':
                return httpx.Response(200, json=cart_payload())
            return httpx.Response(
                200,
                json=cart_payload(
                    version=9,
                    total='4.00',
                    product_id='30',
                    name='Pan',
                    quantity=2,
                    unit_price='2.00',
                ),
            )
        if request.url.path.endswith('/cart/') and request.method == 'PUT':
            put_count += 1
            remote = 'other'
            return httpx.Response(503)
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MercadonaAccountClient(state_path=state, client=http)
    plan = client.preview_cart_update(
        [{'product_id': '20', 'quantity': 1}],
        mode='replace',
        expected_version=7,
        max_total=Decimal('5'),
    )

    with pytest.raises(ProviderError, match='differs from both'):
        client.commit_cart_update(plan)

    assert put_count == 1
    http.close()
