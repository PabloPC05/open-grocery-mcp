"""Mocked-transport tests for the Froiz HTTP client contract."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from open_grocery_mcp.errors import AuthenticationRequired
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient


def _state(tmp_path: Path) -> Path:
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    return path


def _processed_cart(cart_id: str, qty: float = 1) -> dict:
    return {
        "id": cart_id,
        "items": [
            {
                "comment": "",
                "enabled": True,
                "product": {"id": "p-milk", "name": "Leche", "price": 1.65},
                "qty": qty,
                "unit": "ud",
            }
        ],
        "total": round(1.65 * qty, 2),
        "userId": "<user>",
    }


def _router(
    requests: list[httpx.Request],
    *,
    raw_payload: dict | None = None,
    unauthorized_first: bool = False,
) -> httpx.MockTransport:
    state = {"unauthorized_seen": False}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/api/me":
            if unauthorized_first and not state["unauthorized_seen"]:
                state["unauthorized_seen"] = True
                return httpx.Response(401, text="Unauthorized")
            return httpx.Response(
                200,
                json={
                    "id": "user-1",
                    "userAddresses": [
                        {
                            "id": "addr-1",
                            "isDefault": True,
                            "postalCode": "28050",
                            "street": "Calle Secreta",
                        }
                    ],
                    "userChannelOptions": [
                        {"channelName": "shop", "cartId": "cart-uuid"}
                    ],
                },
            )
        if path.startswith("/api/stores/postalcode/"):
            return httpx.Response(
                200,
                json={
                    "id": "store-7",
                    "codEnt": "E1",
                    "codSubent": "S2",
                    "hasDelivery": True,
                },
            )
        if path.startswith("/api/deliverymatrix/calendar/"):
            assert path == "/api/deliverymatrix/calendar/E1_S2"
            return httpx.Response(
                200,
                json={
                    "deliveryCalendar": [
                        {
                            "date": "2026-08-22",
                            "active": True,
                            "slots": [
                                {"slotText": "10:00 - 12:00", "slotNumber": 0, "active": True},
                                {"slotText": "12:00 - 14:00", "slotNumber": 1, "active": False},
                            ],
                        }
                    ]
                },
            )
        if path.startswith("/api/cart/raw/"):
            payload = raw_payload if raw_payload is not None else _processed_cart("cart-uuid")
            return httpx.Response(200, json=payload)
        if path == "/api/cart" and request.method == "POST":
            body = json.loads(request.content)
            cart = _processed_cart("new-cart")
            cart["items"] = [
                {**item, "enabled": True, "product": {"id": item["product_id"], "name": "Leche", "price": 1.65}}
                for item in body.get("items", [])
            ]
            return httpx.Response(201, json=cart)
        if path.startswith("/api/cart/") and request.method == "PUT":
            body = json.loads(request.content)
            cart_id = path.rsplit("/", 1)[-1]
            cart = _processed_cart(cart_id)
            cart["items"] = [
                {**item, "enabled": True, "product": {"id": item["product_id"], "name": "Leche", "price": 1.65}}
                for item in body.get("items", [])
            ]
            cart["total"] = round(sum(i["qty"] * 1.65 for i in cart["items"]), 2)
            return httpx.Response(200, json=cart)
        if path.startswith("/api/cart/") and request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _client(tmp_path: Path, transport: httpx.MockTransport) -> FroizHTTPClient:
    client = FroizHTTPClient(state_path=_state(tmp_path), client=httpx.Client(transport=transport))
    client._token_cache_path = tmp_path / "http_token.json"
    client._token_cache_path.write_text(
        json.dumps({"token": "tok", "fetched_at": time.time()}), encoding="utf-8"
    )
    return client


def test_addresses_are_redacted_and_calendar_normalizes_slots(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))

    addresses = client.addresses()
    assert addresses == [
        {
            "id": "addr-1",
            "is_default": True,
            "field_names": [
                "id",
                "isDefault",
                "postalCode",
                "street",
            ],
        }
    ]
    serialized = json.dumps(addresses)
    assert "Calle Secreta" not in serialized
    assert "28050" not in serialized

    slots = client.delivery_calendar("28050")
    assert len(slots) == 2
    first, second = slots
    assert (first["date"], first["start"], first["end"]) == (
        "2026-08-22",
        "10:00",
        "12:00",
    )
    assert first["available"] is True and second["available"] is False

    store_call = next(
        r for r in requests if r.url.path.startswith("/api/stores/postalcode/")
    )
    assert store_call.url.path == "/api/stores/postalcode/28050"
    calendar_call = next(
        r for r in requests if r.url.path.startswith("/api/deliverymatrix/calendar/")
    )
    assert calendar_call.url.path == "/api/deliverymatrix/calendar/E1_S2"
    urls = json.dumps([str(r.url) for r in requests])
    assert "/orders" not in urls and "/api/payment" not in urls
    client.close()


def test_channel_cart_id_reads_shop_channel(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    assert client.channel_cart_id() == "cart-uuid"
    me_request = next(r for r in requests if r.url.path == "/api/me")
    assert me_request.headers["authorization"] == "Bearer tok"
    serialized = json.dumps([str(r.url) for r in requests])
    assert "/orders" not in serialized
    assert "/api/payment" not in serialized
    client.close()


def test_raw_read_and_normalize_processed_shape(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    normalized = client.normalize_cart(client.raw_cart("cart-uuid"))
    assert normalized["cart_id"] == "cart-uuid"
    assert normalized["products_count"] == 1
    line = normalized["lines"][0]
    assert line["product_id"] == "p-milk"
    assert line["quantity"] == 1.0
    assert line["unit_price"] == 1.65
    assert normalized["total"] == 1.65
    client.close()


def test_create_update_delete_disposable_round_trip(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    items = [{"product_id": "p-milk", "qty": 1, "unit": "ud", "comment": ""}]
    created = client.create_cart(items)
    assert created["id"] == "new-cart"
    updated = client.update_cart("new-cart", items)
    assert updated["items"][0]["qty"] == 1
    client.delete_cart("new-cart")
    methods_paths = [(r.method, r.url.path) for r in requests]
    assert ("POST", "/api/cart") in methods_paths
    assert ("PUT", "/api/cart/new-cart") in methods_paths
    assert ("DELETE", "/api/cart/new-cart") in methods_paths
    assert all("/orders" not in p for _, p in methods_paths)
    assert all(not p.startswith("/api/payment") for _, p in methods_paths)
    client.close()


def test_stable_version_is_content_derived() -> None:
    base = {"items": [{"product_id": "p1", "qty": 2, "unit": "ud"}], "total": "3.3"}
    same_different_order = {
        "items": [{"unit": "ud", "qty": 2, "product_id": "p1"}],
        "total": "3.30",
    }
    changed = {"items": [{"product_id": "p1", "qty": 3, "unit": "ud"}], "total": "4.95"}
    first = FroizHTTPClient.stable_version(base)
    assert FroizHTTPClient.stable_version(same_different_order) == first
    assert FroizHTTPClient.stable_version(changed) != first


def test_expired_token_refreshes_once_via_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    client = _client(
        tmp_path,
        _router(requests, unauthorized_first=True),
    )
    monkeypatch.setattr(
        client, "_bootstrap_token_via_browser", lambda: "fresh-token", raising=True
    )
    cart_id = client.channel_cart_id()
    assert cart_id == "cart-uuid"
    auth_headers = [
        r.headers.get("authorization") for r in requests if r.url.path == "/api/me"
    ]
    assert auth_headers == ["Bearer tok", "Bearer fresh-token"]
    # The fresh token is persisted for subsequent runs.
    cached = json.loads(client._token_cache_path.read_text(encoding="utf-8"))
    assert cached["token"] == "fresh-token"
    client.close()


def test_missing_session_without_bootstrap_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(
        tmp_path,
        _router(requests, unauthorized_first=True),
    )
    monkeypatch.setattr(client, "_bootstrap_token_via_browser", lambda: None, raising=True)
    with pytest.raises(AuthenticationRequired):
        client.channel_cart_id()
    client.close()
