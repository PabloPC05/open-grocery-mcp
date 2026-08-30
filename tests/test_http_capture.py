from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from tools.capture_http_local import request_body_shape, should_block_request
from tools.http_capture.bundle_scan import endpoint_literals
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

# Skip all tests in this module if playwright is not available
pytest.importorskip("playwright")

from tools.http_capture.probe import Probe


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
    # The live cart lives under the Spanish catch-all route, not the generic
    # "/cart" guess used by other storefronts.
    assert "/pag/proceso-de-compra/carrito" in STORES["gadis"].cart_paths


def test_mercadona_capture_uses_location_aware_storefront() -> None:
    spec = STORES["mercadona"]
    assert spec.base_url == "https://tienda.mercadona.es"
    assert "/cart/" in spec.cart_paths


def test_mercadona_product_discovery_requires_local_location(monkeypatch) -> None:
    from tools.http_capture import common

    monkeypatch.delenv("OPEN_GROCERY_MERCADONA_WAREHOUSE", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_MERCADONA_POSTAL_CODE", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_CAPTURE_POSTAL_CODE", raising=False)

    with pytest.raises(RuntimeError, match="local warehouse or postal code"):
        common.choose_product("mercadona")


def test_mercadona_product_discovery_passes_postal_context(monkeypatch) -> None:
    from tools.http_capture import common

    calls: list[dict[str, object]] = []

    class FakeProvider:
        def __init__(self, **kwargs) -> None:
            calls.append({"init": kwargs})

        def search(self, query, **kwargs):
            calls.append({"query": query, **kwargs})
            return [
                SimpleNamespace(
                    id="123",
                    name="Arroz redondo",
                    url="https://tienda.mercadona.es/product/123/arroz",
                    price=1.25,
                    metadata={"warehouse": "mad1"},
                )
            ]

        def close(self) -> None:
            calls.append({"close": True})

    monkeypatch.setattr(common, "MercadonaProvider", FakeProvider)
    monkeypatch.setenv("OPEN_GROCERY_MERCADONA_POSTAL_CODE", "28050")
    product = common.choose_product("mercadona")

    assert product["id"] == "123"
    assert product["_warehouse"] == "mad1"
    assert any(call.get("postal_code") == "28050" for call in calls)
    assert calls[-1] == {"close": True}


def test_bundle_scanner_extracts_only_relevant_value_free_routes() -> None:
    candidates = endpoint_literals(
        """
        const cart='/api/v3/carts/12345/lines?token=secret';
        const checkout="https://checkout.gadisline.com/api/checkouts/abc123/delivery";
        const image='/assets/logo.svg';
        """,
        "https://www.gadisline.com/_next/static/chunks/app.js",
    )
    assert (
        "https://www.gadisline.com/api/v3/carts/<id>/lines?token=%3Cvalue%3E"
        in candidates
    )
    assert (
        "https://checkout.gadisline.com/api/checkouts/<id>/delivery"
        in candidates
    )
    assert not any("logo.svg" in candidate for candidate in candidates)


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
    assert (
        safe_message("failed at https://supermercado.froiz.com/cart")
        == "failed at https://supermercado.froiz.com/cart"
    )


def test_messages_redact_private_urls_postal_codes_and_addresses() -> None:
    message = safe_message(
        "failed at https://shop.test/api/orders/create?token=abc123 "
        "for postal 28050 Calle Mayor 12"
    )
    assert "abc123" not in message
    assert "28050" not in message
    assert "Mayor 12" not in message


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


def test_manifest_recognizes_eroski_tapestry_cart_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "eroski.json"
    path.write_text(
        json.dumps(
            {
                "store": "eroski",
                "events": [
                    {
                        "kind": "request",
                        "phase": "cart_add",
                        "method": "POST",
                        "url": (
                            "https://supermercado.eroski.es/es/search/"
                            "results.productlist:addtocart"
                        ),
                        "headers": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = add_manifest(path)
    assert result["manifest_summary"]["retailer_endpoint_count"] == 1
    assert result["retailer_endpoint_manifest"][0]["operation_hint"] == "cart"


def test_manifest_recognizes_gadis_update_product_as_cart_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gadis.json"
    path.write_text(
        json.dumps(
            {
                "store": "gadis",
                "events": [
                    {
                        "kind": "request",
                        "phase": "quantity_2",
                        "method": "PUT",
                        "url": "https://www.gadisline.com/api/config/updateProduct",
                        "headers": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = add_manifest(path)

    assert result["retailer_endpoint_manifest"][0]["operation_hint"] == "cart"


def test_final_order_patterns_are_blocked_but_cart_is_not() -> None:
    assert DANGEROUS.search("https://shop.test/api/checkouts/c1/orders/")
    assert DANGEROUS.search("https://shop.test/api/checkouts/c1/confirm/")
    assert DANGEROUS.search("https://shop.test/api/checkouts/c1/confirm?step=1")
    assert DANGEROUS.search("https://shop.test/api/orders/create")
    assert DANGEROUS.search("https://shop.test/api/order/create")
    assert DANGEROUS.search("https://shop.test/api/payment")
    assert DANGEROUS.search("https://shop.test/api/checkout/confirm")
    assert DANGEROUS.search("https://shop.test/api/checkout/confirmation")
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
    assert should_block_request(
        "checkout_open", "POST", "https://shop.test/api/orders/create"
    )
    assert should_block_request(
        "checkout_open", "POST", "https://shop.test/api/checkouts/c1/confirm/"
    )
    assert should_block_request(
        "checkout_open",
        "POST",
        "https://shop.test/api/action",
        '{"operation":"submitOrder"}',
    )
    assert not should_block_request(
        "cart_add", "POST", "https://shop.test/api/cart/lines"
    )


def test_probe_cleanup_never_overwrites_an_unrecognized_cart_state(
    tmp_path: Path,
) -> None:
    probe = Probe("froiz", "authenticated", tmp_path / "capture.json")
    probe.original_quantity = 0
    probe.last_verified_quantity = 1
    writes = {"count": 0}

    def goto_cart(_page) -> None:
        return None

    def product_quantity(_page) -> int:
        return 2

    def quantity(_page, _value) -> None:
        writes["count"] += 1

    probe.goto_cart = goto_cart
    probe.product_quantity = product_quantity
    probe.quantity = quantity

    with pytest.raises(RuntimeError, match="automatic restoration was refused"):
        probe.cleanup(object())

    assert writes["count"] == 0


def test_probe_order_submit_phase_blocks_every_write_before_navigation(
    tmp_path: Path,
) -> None:
    probe = Probe("mercadona", "authenticated", tmp_path / "capture.json")
    probe.phase = "order_submit_probe"

    class Request:
        method = "PUT"
        url = "https://tienda.mercadona.es/api/customers/<id>/cart/"
        post_data = '{"items": []}'

    class Route:
        request = Request()
        aborted = False

        def abort(self, _reason: str) -> None:
            self.aborted = True

        def continue_(self) -> None:
            raise AssertionError("order probe write escaped the route guard")

    route = Route()
    probe.route(route, route.request)
    assert route.aborted is True
    assert probe.blocked[0]["reason"] == "all writes are blocked during order_submit_probe"


def test_probe_blocks_dangerous_read_routes_too(tmp_path: Path) -> None:
    probe = Probe("mercadona", "guest", tmp_path / "capture.json")

    class Request:
        method = "GET"
        url = "https://tienda.mercadona.es/api/checkout/confirm"
        post_data = None

    class Route:
        request = Request()
        aborted = False

        def abort(self, _reason: str) -> None:
            self.aborted = True

        def continue_(self) -> None:
            raise AssertionError("dangerous route escaped the guard")

    route = Route()
    probe.route(route, route.request)
    assert route.aborted is True
