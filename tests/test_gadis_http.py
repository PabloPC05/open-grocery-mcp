from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    OrderSubmissionDisabled,
    ProviderError,
)
from open_grocery_mcp.providers.gadis_http import GadisHTTPClient

SESSION_URL = "https://www.gadisline.com/api/auth/session"


def _state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "domain": ".gadisline.com",
                        "name": "__Secure-next-auth.session-token",
                        "value": "private-session-cookie",
                        "expires": -1,
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )


def _router(
    requests: list[httpx.Request],
    *,
    profile: dict | None = None,
    addresses: list[dict] | None = None,
    calendar: list[dict] | None = None,
    token: dict | None = None,
    schedule_response: dict | None = None,
    checkout_response: dict | None = None,
) -> httpx.MockTransport:
    session_token = token if token is not None else {"accessToken": "keycloak-jwt"}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        host = request.url.host
        if host == "www.gadisline.com" and request.url.path == "/api/auth/session":
            return httpx.Response(
                200,
                json={"expires": "2030-01-01T00:00:00.000Z", "token": session_token},
            )
        if host == "www.gadisline.com" and request.url.path == "/":
            return httpx.Response(
                200,
                html='<script id="__NEXT_DATA__" type="application/json">{"buildId":"build-1"}</script>',
            )
        if host == "www.gadisline.com" and request.url.path.endswith("/carrito.json"):
            return httpx.Response(
                200,
                json={"pageProps": {"cart": _cart_payload()}},
            )
        if host == "site.gadisline.com":
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {"id": "site-1", "default_assortment_store": "store-7"}
                    ]
                },
            )
        if host == "clients.gadisline.com":
            body = profile if profile is not None else {
                "id": "client-1234567890",
                "given_name": "Given",
                "family_name": "Family",
                "email": "person@example.com",
                "email_verified": True,
                "phone_verified": True,
                "complete_register": True,
                "postal_code": "28050",
            }
            return httpx.Response(200, json=body)
        if host == "cart.gadisline.com":
            path = request.url.path
            if path.endswith("/schedule") and request.method == "PUT":
                body = schedule_response
                return httpx.Response(200, json=body if body is not None else {})
            if path.endswith("/schedule") and request.method == "DELETE":
                return httpx.Response(204)
            if path.endswith("/checkout"):
                assert request.method == "POST"
                body = checkout_response
                return httpx.Response(
                    200,
                    json=body
                    if body is not None
                    else {
                        "id": "checkout-http",
                        "total_cart_price": 7.19,
                        "removed_products": [],
                        "has_product_price_changes": False,
                    },
                )
            elements = addresses if addresses is not None else []
            return httpx.Response(200, json={"elements": elements})
        if host == "store.gadisline.com" and request.url.path.endswith("/calendar"):
            elements = calendar if calendar is not None else []
            return httpx.Response(200, json={"elements": elements})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_gadis_http_profile_uses_bearer_and_context_headers(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=_router(requests))
    account = GadisHTTPClient(state_path=state_path, client=client)
    profile = account.profile()
    assert profile["profile_present"] is True
    assert profile["email_verified"] is True
    assert profile["client_id_suffix"] == "567890"
    assert profile["profile_values_exposed"] is False

    me = next(r for r in requests if r.url.host == "clients.gadisline.com")
    assert me.url.path == "/api/v3/clients/me"
    assert me.headers["authorization"] == "Bearer keycloak-jwt"
    assert me.headers["site-id"] == "site-1"
    assert me.headers["store-id"] == "store-7"
    serialized = json.dumps(profile)
    assert "person@example.com" not in serialized
    assert "client-1234567890" not in serialized
    account.close()


def test_gadis_http_honors_explicit_site_and_store_context(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=_router(requests))
    account = GadisHTTPClient(
        state_path=state_path,
        site_id="site-explicit",
        store_id="store-explicit",
        client=client,
    )

    account.profile()

    me = next(r for r in requests if r.url.host == "clients.gadisline.com")
    assert me.headers["site-id"] == "site-explicit"
    assert me.headers["store-id"] == "store-explicit"
    account.close()


def test_gadis_http_status_is_value_free(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=_router(requests))
    account = GadisHTTPClient(state_path=state_path, client=client)
    status = account.status()
    assert status["authenticated"] is True
    assert status["bearer_token_available"] is True
    serialized = json.dumps(status)
    assert "keycloak-jwt" not in serialized
    assert "private-session-cookie" not in serialized
    account.close()


def test_gadis_http_addresses_are_redacted(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(
        transport=_router(
            requests,
            addresses=[
                {"id": "addr-1", "street": "Calle Secreta 1", "postal_code": "28050"},
            ],
        )
    )
    account = GadisHTTPClient(state_path=state_path, client=client)
    result = account.addresses("cart-42")
    assert result == [
        {
            "id": "addr-1",
            "owner": None,
            "field_names": ["id", "postal_code", "street"],
        }
    ]
    cart = next(r for r in requests if r.url.host == "cart.gadisline.com")
    assert cart.url.path == "/api/v3/carts/cart-42/addresses"
    assert cart.headers["authorization"] == "Bearer keycloak-jwt"
    serialized = json.dumps(result)
    assert "Calle Secreta" not in serialized
    assert "28050" not in serialized
    account.close()


def test_gadis_http_encodes_cart_identity_as_one_path_segment(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=_router(requests))
    account = GadisHTTPClient(state_path=state_path, client=client)

    account.addresses("cart/42 ?")

    cart = next(r for r in requests if r.url.host == "cart.gadisline.com")
    assert cart.url.raw_path == b"/api/v3/carts/cart%2F42%20%3F/addresses"
    account.close()


def test_gadis_http_delivery_slots_normalize_calendar(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(
        transport=_router(
            requests,
            calendar=[
                {
                    "date": "2026-08-25",
                    "schedule_ranges": [
                        {
                            "id": "slot-9",
                            "init_time": "10:00",
                            "end_time": "11:00",
                            "available": True,
                            "active": True,
                            "max_lines": 8,
                        }
                    ],
                }
            ],
        )
    )
    account = GadisHTTPClient(state_path=state_path, client=client)
    slots = account.delivery_slots(
        "28050",
        init_date="2026-08-25",
        end_date="2026-08-31",
    )
    assert slots == [
        {
            "id": "slot-9",
            "date": "2026-08-25",
            "start": "10:00",
            "end": "11:00",
            "available": True,
            "active": True,
            "max_lines": 8,
        }
    ]
    calendar = next(
        r for r in requests if r.url.host == "store.gadisline.com" and r.url.path.endswith("/calendar")
    )
    assert calendar.url.params["postal_code"] == "28050"
    assert calendar.url.params["delivery_type"] == "HOME_DELIVERY"
    assert calendar.url.params["init_date"] == "2026-08-25"
    assert calendar.url.path.endswith("/stores/store-7/calendar")
    account.close()


def test_gadis_http_encodes_selected_store_identity(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=_router(requests))
    account = GadisHTTPClient(state_path=state_path, client=client)

    account.delivery_slots("28050", store_id="store/7 ?")

    calendar = next(r for r in requests if r.url.host == "store.gadisline.com")
    assert calendar.url.raw_path.split(b"?")[0] == (
        b"/api/v3/stores/store%2F7%20%3F/calendar"
    )
    account.close()


def test_gadis_http_unauthorized_session_raises(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "www.gadisline.com":
            return httpx.Response(200, json={"expires": "2030-01-01", "token": {"accessToken": "jwt"}})
        if request.url.host == "site.gadisline.com":
            return httpx.Response(
                200,
                json={"elements": [{"id": "site-1", "default_assortment_store": "store-7"}]},
            )
        return httpx.Response(401, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    account = GadisHTTPClient(state_path=state_path, client=client)
    with pytest.raises(AuthenticationRequired):
        account.profile()
    account.close()


def _cart_payload() -> dict:
    return {
        "id": "cart-1",
        "store_id": "store-7",
        "products": [
            {
                "product_id": "p-eggs",
                "product_name": "Huevos",
                "amount": 1,
                "line_price": 7.19,
            },
            {
                "product_id": "p-milk",
                "product_name": "Leche entera",
                "amount": 2,
                "line_price": 2.12,
            },
        ],
        "total_cart_price": 9.31,
        "total_products": 2,
        "last_modified_date": 1710000000000,
    }


def test_gadis_http_normalization_preserves_non_product_costs() -> None:
    payload = _cart_payload()
    payload.update({"total_product_price": 2.85, "costs": 4.50, "total_cart_price": 7.35})

    normalized = GadisHTTPClient.normalize_cart(payload)

    assert normalized["total_product_price"] == 2.85
    assert normalized["non_product_costs"] == 4.5
    assert normalized["total"] == 7.35


def test_gadis_http_rejects_malformed_non_product_costs() -> None:
    payload = _cart_payload()
    payload["costs"] = "not-a-price"

    with pytest.raises(ProviderError, match="non-product costs"):
        GadisHTTPClient.normalize_cart(payload)


def _cart_router(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        host = request.url.host
        path = request.url.path
        if host == "www.gadisline.com":
            if path == "/api/auth/session":
                return httpx.Response(
                    200,
                    json={"expires": "2030-01-01", "token": {"accessToken": "keycloak-jwt"}},
                )
            if path == "/":
                return httpx.Response(
                    200,
                    html='<script id="__NEXT_DATA__" type="application/json">{"buildId":"build-1"}</script>',
                )
            if path == "/api/config/updateProduct":
                assert request.method == "PUT"
                assert request.headers["authorization"] == "Bearer keycloak-jwt"
                body = json.loads(request.content)
                assert body["cartId"] == "cart-1"
                assert body["amount"] == 0
                assert body["summaryCheckout"] is False
                return httpx.Response(200, json=_cart_payload())
            if "/_next/data/" in path and path.endswith("/carrito.json"):
                assert path == "/_next/data/build-1/es/pag/proceso-de-compra/carrito.json"
                return httpx.Response(200, json={"pageProps": {"cart": _cart_payload()}})
        if host == "site.gadisline.com":
            return httpx.Response(
                200,
                json={"elements": [{"id": "site-1", "default_assortment_store": "store-7"}]},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_gadis_http_reads_and_mutates_cart(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=_cart_router(requests))
    account = GadisHTTPClient(state_path=state_path, client=client)

    cart = account.cart()
    assert cart["cart_id"] == "cart-1"
    assert cart["products_count"] == 2
    assert cart["total"] == 9.31
    assert [line["product_id"] for line in cart["lines"]] == ["p-eggs", "p-milk"]

    updated = account.update_product("cart-1", "store-7", "p-milk", 0)
    assert updated["products_count"] == 2
    assert updated["retailer_cart_modified"] is True
    serialized = json.dumps(updated)
    assert "keycloak-jwt" not in serialized
    account.close()


def test_gadis_http_normalize_cart_is_value_free() -> None:
    normalized = GadisHTTPClient.normalize_cart(_cart_payload())
    assert normalized["cart_id"] == "cart-1"
    assert normalized["lines"][1]["quantity"] == 2.0
    assert normalized["lines"][1]["line_price"] == 2.12
    assert normalized["lines"][1]["unit_price"] == 1.06
    assert normalized["lines"][1]["line_total"] == 2.12


def test_gadis_http_version_is_stable_across_reads_with_volatile_timestamp() -> None:
    first = GadisHTTPClient.normalize_cart(_cart_payload())
    refetched = _cart_payload()
    # The retailer bumps last_modified_date on every cart fetch.
    refetched["last_modified_date"] = refetched["last_modified_date"] + 2312
    second = GadisHTTPClient.normalize_cart(refetched)
    assert second["version"] == first["version"]
    assert second["version"] != 1710000000000


def test_gadis_http_version_tracks_cart_content() -> None:
    baseline = GadisHTTPClient.normalize_cart(_cart_payload())["version"]

    quantity_changed = _cart_payload()
    quantity_changed["products"][1]["amount"] = 3
    quantity_changed["products"][1]["line_price"] = 3.18
    quantity_changed["total_products"] = 3
    quantity_changed["total_cart_price"] = 11.43
    assert (
        GadisHTTPClient.normalize_cart(quantity_changed)["version"] != baseline
    )

    total_changed = _cart_payload()
    total_changed["total_cart_price"] = 9.32
    assert GadisHTTPClient.normalize_cart(total_changed)["version"] != baseline


def test_gadis_http_schedule_write_and_delete(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(
        transport=_router(requests, schedule_response=_cart_payload())
    )
    account = GadisHTTPClient(state_path=state_path, client=client)

    updated = account.update_schedule(
        "cart-1",
        "store-7",
        delivery_date="2026-08-25",
        schedule_range_id="slot-9",
    )
    assert updated["cart_id"] == "cart-1"
    put = next(
        r
        for r in requests
        if r.url.host == "cart.gadisline.com" and r.method == "PUT"
    )
    assert put.url.path == "/api/v3/carts/cart-1/schedule"
    assert json.loads(put.content) == {
        "delivery_date": "2026-08-25",
        "schedule_range_id": "slot-9",
    }
    assert put.headers["authorization"] == "Bearer keycloak-jwt"

    assert account.delete_schedule("cart-1") is None
    delete = next(
        r
        for r in requests
        if r.url.host == "cart.gadisline.com" and r.method == "DELETE"
    )
    assert delete.url.path == "/api/v3/carts/cart-1/schedule"
    account.close()


def test_gadis_schedule_does_not_fallback_after_ambiguous_config_failure(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    base = _router(requests)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/config/updateCart":
            requests.append(request)
            return httpx.Response(500)
        return base.handle_request(request)

    account = GadisHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderError, match="HTTP 500"):
        account.update_schedule(
            "cart-1",
            "store-7",
            delivery_date="2026-08-25",
            schedule_range_id="slot-9",
        )
    assert not any(
        request.url.host == "cart.gadisline.com"
        and request.url.path.endswith("/schedule")
        for request in requests
    )


def test_gadis_payment_bearing_checkout_route_is_blocked_before_network(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=_router(requests))
    account = GadisHTTPClient(state_path=state_path, client=client)

    with pytest.raises(OrderSubmissionDisabled, match="payment and terms"):
        account.create_checkout("cart-1", "store-7")
    assert requests == []
    account.close()


def test_gadis_prepares_and_restores_checkout_summary_without_payment_post(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    base = _router(requests)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/config/updateCart":
            requests.append(request)
            body = json.loads(request.content)
            payload = _cart_payload()
            payload.update(
                {
                    "store_id": body["store_id"],
                    "postal_code": body["postal_code"],
                    "delivery_type": body["delivery_type"],
                    "comments": body["comments"],
                    "shipping_address_id": body["shipping_address_id"],
                    "shipping_address_owner": body["shipping_address_owner"],
                    "delivery_date": body.get("delivery_date"),
                    "schedule_range_id": body.get("schedule_range_id"),
                }
            )
            return httpx.Response(200, json=payload)
        return base.handle_request(request)

    account = GadisHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    prepared = account.prepare_checkout_summary(
        "cart-1",
        "store-7",
        shipping_address_id="addr-1",
        shipping_address_owner="CLIENT",
        delivery_date="2026-08-25",
        schedule_range_id="slot-9",
        postal_code="28050",
    )
    assert prepared["summary_prepared"] is True

    baseline = _cart_payload()
    baseline.update(
        {
            "store_id": "store-7",
            "postal_code": "28050",
            "delivery_type": "HOME_DELIVERY",
            "comments": "",
            "shipping_address_id": None,
            "shipping_address_owner": None,
        }
    )
    account.restore_cart_context(baseline)

    writes = [r for r in requests if r.url.path == "/api/config/updateCart"]
    assert len(writes) == 2
    prepare_body = json.loads(writes[0].content)
    assert prepare_body == {
        "store_id": "store-7",
        "postal_code": "28050",
        "delivery_type": "HOME_DELIVERY",
        "comments": "",
        "shipping_address_id": "addr-1",
        "shipping_address_owner": "CLIENT",
        "delivery_date": "2026-08-25",
        "schedule_range_id": "slot-9",
        "save_order_time": True,
        "summaryCheckout": True,
    }
    restore_body = json.loads(writes[1].content)
    assert restore_body["summaryCheckout"] is False
    assert restore_body["save_order_time"] is False
    assert restore_body["shipping_address_id"] == ""
    assert restore_body["shipping_address_owner"] == ""
    unsafe_words = ("checkout", "order", "payment", "redsys")
    assert not any(
        request.method == "POST"
        and any(word in request.url.path.casefold() for word in unsafe_words)
        for request in requests
    )
    account.close()


def test_gadis_checkout_post_stays_blocked_regardless_of_terms(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    requests: list[httpx.Request] = []
    account = GadisHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=_router(requests)),
    )
    with pytest.raises(OrderSubmissionDisabled):
        account.create_checkout(
            "cart-1",
            "store-7",
            shipping_address_id="addr-1",
            delivery_date="2026-08-25",
            schedule_range_id="slot-9",
            terms_and_conditions=False,
        )
    assert requests == []


def test_gadis_request_errors_redact_private_route_ids(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    account = GadisHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    account._site_id = "site-1"
    account._store_id = "store-7"
    account._access_token = "private-token"
    with pytest.raises(ProviderError) as raised:
        account._request(
            "GET",
            "https://cart.gadisline.com/api/v3/carts/private-cart/checkout",
        )
    message = str(raised.value)
    assert "private-cart" not in message
    assert "private-token" not in message
    assert "/carts/<private>/checkout" in message
    assert raised.value.status_code == 500

