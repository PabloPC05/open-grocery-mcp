from __future__ import annotations

import json
from pathlib import Path

from tools.capture_http_local import request_body_shape, should_block_request
from tools.http_capture.common import (
    DANGEROUS,
    STORES,
    _browser_product_url,
    safe_headers,
    safe_message,
    safe_url,
    shape,
)
from tools.http_capture.manifest import add_manifest


def test_safe_url_keeps_route_but_removes_values() -> None:
    result = safe_url(
        "https://shop.test/api/customers/550e8400-e29b-41d4-a716-446655440000/cart"
        "?token=secret&store=42"
    )
    assert result == (
        "https://shop.test/api/customers/<id>/cart?token=%3Cvalue%3E&store=%3Cvalue%3E"
    )
    assert "secret" not in result
    assert "550e8400" not in result


def test_safe_url_redacts_short_private_ids_but_keeps_public_product_ids() -> None:
    assert safe_url("https://shop.test/api/addresses/42/slots") == (
        "https://shop.test/api/addresses/<id>/slots"
    )
    assert safe_url("https://shop.test/api/customers/7/cart/12") == (
        "https://shop.test/api/customers/<id>/cart/<id>"
    )
    assert safe_url("https://shop.test/api/products/42") == (
        "https://shop.test/api/products/42"
    )
    assert safe_url("https://shop.test/api/cart/items") == (
        "https://shop.test/api/cart/items"
    )


def test_gadis_capture_uses_current_resolvable_storefront() -> None:
    assert STORES["gadis"].base_url == "https://www.gadisline.com"
    url = "https://www.gadisline.com/product/leche?campaign=1#private"
    assert _browser_product_url("gadis", url) == url


def test_shape_redacts_generic_and_account_identifiers() -> None:
    value = shape(
        {
            "id": "cart-private-id",
            "customer_id": "customer-private-id",
            "product_id": "sku-123",
            "quantity": 2,
            "email": "person@example.com",
            "status": "open",
        }
    )
    assert value == {
        "id": "<id>",
        "customer_id": "<redacted>",
        "product_id": "sku-123",
        "quantity": 2,
        "email": "<redacted>",
        "status": "open",
    }


def test_headers_and_messages_never_keep_secrets(monkeypatch) -> None:
    monkeypatch.setenv("GADIS_TEST_PASSWORD", "disposable-password")
    headers = safe_headers(
        {
            "Authorization": "Bearer abc.def.ghi",
            "Referer": (
                "https://shop.test/account/"
                "550e8400-e29b-41d4-a716-446655440000?token=x"
            ),
            "X-CSRF-Token": "private",
            "X-Store-Id": "gadis-1",
        }
    )
    assert headers["Authorization"] == "<redacted>"
    assert headers["X-CSRF-Token"] == "<redacted>"
    assert "550e8400" not in headers["Referer"]
    assert headers["X-Store-Id"] == "gadis-1"
    message = safe_message(
        "failed for disposable-password person@example.com Bearer abc.def.ghi"
    )
    assert "disposable-password" not in message
    assert "person@example.com" not in message
    assert "abc.def.ghi" not in message


def test_manifest_associates_endpoint_with_capture_phase(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "store": "gadis",
                "events": [
                    {
                        "kind": "request",
                        "phase": "add",
                        "method": "POST",
                        "url": "https://shop.test/api/cart/items?store=%3Cvalue%3E",
                        "headers": {"content-type": "application/json"},
                        "body": {"product_id": "sku-123", "quantity": 1},
                    },
                    {
                        "kind": "response",
                        "phase": "add",
                        "method": "POST",
                        "url": "https://shop.test/api/cart/items?store=%3Cvalue%3E",
                        "status": 201,
                        "headers": {},
                        "body": {"id": "<id>", "version": 4},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    result = add_manifest(path)
    endpoint = result["endpoint_manifest"][0]
    assert endpoint["method"] == "POST"
    assert endpoint["path"] == "/api/cart/items"
    assert endpoint["query_keys"] == ["store"]
    assert endpoint["phases"] == ["add"]
    assert endpoint["response_statuses"] == [201]
    assert endpoint["request_body_schema"] == {
        "product_id": "string",
        "quantity": "integer",
    }


def test_manifest_publishes_a_retailer_only_view(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "store": "gadis",
                "events": [
                    {
                        "kind": "request",
                        "phase": "cart",
                        "method": "PUT",
                        "url": "https://cart.gadisline.com/api/v3/carts/42/lines",
                        "headers": {"content-type": "application/json"},
                        "body": {"product_id": "sku-1", "quantity": 2},
                    },
                    {
                        "kind": "response",
                        "phase": "cart",
                        "method": "PUT",
                        "url": "https://cart.gadisline.com/api/v3/carts/42/lines",
                        "status": 200,
                        "headers": {},
                        "body": {"version": 4},
                    },
                    {
                        "kind": "request",
                        "phase": "cart",
                        "method": "POST",
                        "url": "https://analytics.google.com/g/collect",
                        "headers": {},
                        "body": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    result = add_manifest(path)
    assert len(result["endpoint_manifest"]) == 2
    assert len(result["retailer_endpoint_manifest"]) == 1
    endpoint = result["retailer_endpoint_manifest"][0]
    assert endpoint["host"] == "cart.gadisline.com"
    assert endpoint["operation_hint"] == "cart"
    assert result["manifest_summary"]["operation_counts"] == {"cart": 1}


def test_final_order_patterns_are_blocked_but_cart_is_not() -> None:
    assert DANGEROUS.search("https://shop.test/api/checkouts/c1/orders/")
    assert DANGEROUS.search("https://shop.test/api/payment")
    assert not DANGEROUS.search("https://shop.test/api/cart/items")
    assert not DANGEROUS.search("https://shop.test/api/checkouts/c1/delivery")


class _Request:
    def __init__(self, body: str, content_type: str) -> None:
        self.post_data = body
        self.headers = {"content-type": content_type}


def test_local_capture_redacts_login_form_values() -> None:
    value = request_body_shape(
        _Request(
            "email=person%40example.com&password=disposable&remember=true",
            "application/x-www-form-urlencoded",
        )
    )
    assert value == {
        "email": "<redacted>",
        "password": "<redacted>",
        "remember": "<str>",
    }


def test_local_order_probe_blocks_writes_before_they_leave_browser() -> None:
    assert should_block_request(
        "order_submit_probe", "POST", "https://shop.test/api/cart/lines"
    )
    assert should_block_request(
        "checkout_open", "POST", "https://shop.test/api/orders"
    )
    assert not should_block_request(
        "cart_add", "POST", "https://shop.test/api/cart/lines"
    )
