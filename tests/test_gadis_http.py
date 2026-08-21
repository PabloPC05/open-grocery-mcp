from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from open_grocery_mcp.errors import AuthenticationRequired
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
    assert result == [{"field_names": ["id", "postal_code", "street"]}]
    cart = next(r for r in requests if r.url.host == "cart.gadisline.com")
    assert cart.url.path == "/api/v3/carts/cart-42/addresses"
    assert cart.headers["authorization"] == "Bearer keycloak-jwt"
    serialized = json.dumps(result)
    assert "Calle Secreta" not in serialized
    assert "addr-1" not in serialized
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
    assert calendar.url.params["delivery_type"] == "delivery"
    assert calendar.url.params["init_date"] == "2026-08-25"
    assert calendar.url.path.endswith("/stores/store-7/calendar")
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
                "line_price": 1.06,
            },
        ],
        "total_cart_price": 9.31,
        "total_products": 2,
        "last_modified_date": 1710000000000,
    }


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
    assert normalized["lines"][1]["line_price"] == 1.06


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
    quantity_changed["total_products"] = 3
    quantity_changed["total_cart_price"] = 11.43
    assert (
        GadisHTTPClient.normalize_cart(quantity_changed)["version"] != baseline
    )

    total_changed = _cart_payload()
    total_changed["total_cart_price"] = 9.32
    assert GadisHTTPClient.normalize_cart(total_changed)["version"] != baseline

