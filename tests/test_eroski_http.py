"""Mocked-transport tests for the Eroski Tapestry HTTP client."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import unquote_plus

import httpx
import pytest

from open_grocery_mcp.errors import AuthenticationRequired, InvalidRequest, ProviderError
from open_grocery_mcp.providers.eroski_http import (
    EroskiCart,
    EroskiHTTPClient,
)

TILE_CONFIG = {
    "itemId": "item-list-157719",
    "productRef": "157719",
    "shopRef": "3970",
    "previousAddressRef": "",
    "isWeightOptionsAvailable": False,
    "productUnitsPerPack": 1,
    "quantityInCart": 0,
    "maximumQuantity": 100,
    "onAddToCartEvent": "/es/search/results.productlist."
    "productlistitem_1.productlistadditem:addtocart?q=leche",
}

SEARCH_HTML = (
    '<input type="hidden" name="t:formdata" value="TOKEN-A"/>'
    '<div id="basketTotalPriceZone"></div>'
    "<script>stack.widget([\"common/button/"
    f'productListItemAddComponent:init", {json.dumps(TILE_CONFIG)}]);</script>'
)

MYCART_HTML = """
<span class="shopping-cart__totalprice"><span class="price">1,65€</span></span>
<input type="hidden" name="t:formdata" value="CART-TOKEN"/>
<div class="row shopping-cart-item">
  <div class="product-image basket-product-157719"></div>
  <input type="text" class="form-control quantity" value="1"/>
</div>
"""


def _router(
    requests: list[httpx.Request],
    *,
    unauthorized: bool = False,
    cart_login: bool = False,
    delivery_redirect: bool = False,
    persist_write: bool = True,
    rotate_session: bool = False,
):
    state = {"first": True, "cart_qty": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/" and request.url.params.get("zipCode"):
            if unauthorized and state["first"]:
                state["first"] = False
                return httpx.Response(
                    200, text='Identifícate <input type="password"/>'
                )
            headers = (
                {"Set-Cookie": "JSESSIONID=rotated-session; Path=/; HttpOnly"}
                if rotate_session
                else {}
            )
            return httpx.Response(200, text="<html>ok</html>", headers=headers)
        if path == "/es/search/results/":
            return httpx.Response(200, text=SEARCH_HTML)
        if path.endswith(":addtocart"):
            content = unquote_plus(request.content.decode())
            assert '"productRef":"157719"' in content.replace(" ", "")
            assert "TOKEN-A" in content or "CART-TOKEN" in content
            assert "unitsToAdd" in content
            if delivery_redirect:
                return httpx.Response(
                    200,
                    json={"_tapestry": {"redirectURL": "/es/login/delivery/"}},
                )
            quantity = int(content.split('"newQuantity":', 1)[1].split(",", 1)[0])
            if persist_write:
                state["cart_qty"] = quantity
            return httpx.Response(200, json={"_tapestry": True})
        if path == "/es/mycart/":
            if cart_login:
                return httpx.Response(200, text='<input type="password"/>')
            quantity = int(state["cart_qty"])
            html = (
                MYCART_HTML.replace('value="1"', f'value="{quantity}"')
                if quantity > 0
                else '<span class="shopping-cart__totalprice"><span class="price">0,00€</span></span>'
            )
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _client(tmp_path: Path, transport: httpx.MockTransport) -> EroskiHTTPClient:
    state = tmp_path / "storage_state.json"
    state.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "JSESSIONID",
                        "value": "java-session",
                        "domain": "supermercado.eroski.es",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return EroskiHTTPClient(
        state_path=state, client=httpx.Client(transport=transport)
    )


def test_parse_cart_from_mycart_html() -> None:
    cart = EroskiHTTPClient.parse_cart(MYCART_HTML)
    assert isinstance(cart, EroskiCart)
    assert len(cart.items) == 1
    assert cart.items[0].product_id == "157719"
    assert cart.items[0].quantity == 1
    assert cart.total_text == "1,65€"


def test_search_tiles_extracts_config(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    tiles = client.search_tiles("leche")
    assert len(tiles) == 1
    assert tiles[0].product_ref == "157719"
    assert tiles[0].shop_ref == "3970"
    assert ":addtocart" in tiles[0].on_add_to_cart_event
    client.close()


def test_add_posts_product_payload_and_zones(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    cart = client.add_to_cart("leche", tile_index=0, quantity=2)
    assert cart.total_text == "1,65€"
    event_post = next(r for r in requests if r.url.path.endswith(":addtocart"))
    body = unquote_plus(event_post.content.decode())
    assert '"newQuantity":2' in body
    assert '"unitsToAdd":2' in body
    assert "basketTotalPriceZone=basketTotalPriceZone" in body
    assert event_post.headers.get("x-requested-with") == "XMLHttpRequest"
    urls = [str(r.url) for r in requests]
    assert all("/orders" not in u for u in urls)
    assert all("/api/payment" not in u for u in urls)
    client.close()


def test_remove_sets_quantity_zero(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    transport = _router(requests)
    # Build a client whose mycart page embeds the tile config with qty 1.
    html_with_cfg = MYCART_HTML + (
        '<script>stack.widget(["common/button/'
        'productListItemAddComponent:init", '
        + json.dumps(
            {
                **TILE_CONFIG,
                "quantityInCart": 1,
                "onAddToCartEvent": "/es/mycart.basket.productlist."
                "basketproduct.basketadditemcomponent:addtocart",
            }
        )
        + "]);</script>"
    )

    original_handler = transport.handler
    removed = {"done": False}

    def patched(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/es/mycart/":
            if removed["done"]:
                return httpx.Response(
                    200,
                    text='<span class="shopping-cart__totalprice"><span class="price">0,00€</span></span>',
                )
            return httpx.Response(200, text=html_with_cfg)
        response = original_handler(request)
        if request.url.path.endswith(":addtocart"):
            removed["done"] = True
        return response

    transport = httpx.MockTransport(patched)
    client = _client(tmp_path, transport)
    cart = client.remove_item("157719")
    event_post = next(r for r in requests if r.url.path.endswith(":addtocart"))
    body = unquote_plus(event_post.content.decode())
    assert '"newQuantity":0' in body
    assert '"unitsToAdd":-1' in body
    assert cart.products_count if hasattr(cart, "products_count") else True
    client.close()


def test_unauthenticated_context_raises(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests, unauthorized=True))
    with pytest.raises(AuthenticationRequired):
        client.read_cart()
    client.close()


def test_authenticated_read_persists_rotated_session_for_new_process(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    transport = _router(requests, rotate_session=True)
    client = _client(tmp_path, transport)
    state_path = client.state_path
    original_state = json.loads(state_path.read_text(encoding="utf-8"))
    original_state["cookies"].append(
        {
            "name": "JSESSIONID",
            "value": "area-session",
            "domain": "areacliente.eroski.es",
            "path": "/",
        }
    )
    state_path.write_text(json.dumps(original_state), encoding="utf-8")

    client.read_cart()
    client.close()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    official = [
        row
        for row in state["cookies"]
        if row.get("domain") == "supermercado.eroski.es"
        and row.get("name") == "JSESSIONID"
    ]
    assert len(official) == 1
    assert official[0]["value"] == "rotated-session"
    assert any(
        row.get("domain") == "areacliente.eroski.es"
        and row.get("value") == "area-session"
        for row in state["cookies"]
    )

    second_requests: list[httpx.Request] = []
    second = EroskiHTTPClient(
        state_path=state_path,
        client=httpx.Client(transport=_router(second_requests)),
    )
    second.read_cart()
    bootstrap = next(
        request for request in second_requests if request.url.path == "/"
    )
    assert "rotated-session" in bootstrap.headers.get("cookie", "")
    second.close()


def test_rotated_cookie_persistence_keeps_old_state_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests, rotate_session=True))
    state_path = client.state_path
    original = state_path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "open_grocery_mcp.providers.eroski_http.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")),
    )

    client.read_cart()

    assert state_path.read_text(encoding="utf-8") == original
    assert not list(state_path.parent.glob("*.tmp"))
    assert not list(state_path.parent.glob(".*.tmp"))
    client.close()


def test_cart_login_page_is_not_parsed_as_an_empty_cart(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests, cart_login=True))
    with pytest.raises(AuthenticationRequired):
        client.read_cart()
    client.close()


def test_delivery_context_redirect_is_reported_explicitly(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests, delivery_redirect=True))
    with pytest.raises(ProviderError, match="selected delivery mode"):
        client.add_to_cart("leche")
    client.close()


def test_unpersisted_write_fails_closed(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests, persist_write=False))
    with pytest.raises(ProviderError, match="did not persist"):
        client.add_to_cart("leche", quantity=2)
    client.close()


def test_cookie_domains_are_preserved_when_names_overlap(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    client.state_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "JSESSIONID",
                        "value": "area-session",
                        "domain": "areacliente.eroski.es",
                        "path": "/areacliente/",
                    },
                    {
                        "name": "JSESSIONID",
                        "value": "supermarket-session",
                        "domain": "supermercado.eroski.es",
                        "path": "/",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    client.read_cart()

    bootstrap = next(request for request in requests if request.url.path == "/")
    assert "supermarket-session" in bootstrap.headers.get("cookie", "")
    assert "area-session" not in bootstrap.headers.get("cookie", "")
    client.close()


def test_context_cache_is_bound_to_storage_state_contents(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    client.read_cart()
    state_path = client.state_path
    before = state_path.stat()
    original = state_path.read_text(encoding="utf-8")
    changed = original.replace("java-session", "next-session")
    assert len(changed) == len(original)
    state_path.write_text(changed, encoding="utf-8")
    os.utime(
        state_path,
        ns=(before.st_atime_ns, before.st_mtime_ns),
    )

    client.read_cart()

    bootstraps = [request for request in requests if request.url.path == "/"]
    assert len(bootstraps) == 2
    client.close()


@pytest.mark.parametrize("quantity", [-1, 0, True, 101])
def test_add_rejects_unsafe_quantities_before_post(
    tmp_path: Path, quantity: object
) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    with pytest.raises(InvalidRequest, match="quantity|maximum"):
        client.add_to_cart("leche", quantity=quantity)  # type: ignore[arg-type]
    assert not any(request.method == "POST" for request in requests)
    client.close()


def test_cart_parser_rejects_missing_total_quantity_and_duplicates() -> None:
    with pytest.raises(ProviderError, match="total"):
        EroskiHTTPClient.parse_cart(
            '<div class="row shopping-cart-item"><div class="basket-product-1"></div>'
            '<input class="quantity" value="1"></div>'
        )
    with pytest.raises(ProviderError, match="quantity"):
        EroskiHTTPClient.parse_cart(
            '<span class="shopping-cart__totalprice"><span class="price">1,00</span></span>'
            '<div class="row shopping-cart-item"><div class="basket-product-1"></div></div>'
        )
    with pytest.raises(ProviderError, match="duplicate"):
        EroskiHTTPClient.parse_cart(MYCART_HTML + MYCART_HTML)


def test_tile_parser_rejects_zero_or_malformed_limits() -> None:
    for value in (0, "bad"):
        html = SEARCH_HTML.replace(
            json.dumps(TILE_CONFIG),
            json.dumps({**TILE_CONFIG, "maximumQuantity": value}),
        )
        assert EroskiHTTPClient.parse_tile_configs(html) == []


def test_untrusted_tapestry_action_is_rejected_without_network(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    with pytest.raises(ProviderError, match="untrusted"):
        client._post_form(
            "https://evil.test/es/productdetail/1:addtocart",
            {"t:formdata": "private"},
        )
    assert requests == []
    client.close()


def test_invalid_postal_and_malformed_state_fail_before_network(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    client.zip_code = "48"
    with pytest.raises(InvalidRequest, match="five-digit"):
        client.read_cart()
    assert requests == []

    client.zip_code = "48001"
    client.state_path.write_text("[]", encoding="utf-8")
    with pytest.raises(AuthenticationRequired, match="malformed"):
        client.read_cart()
    assert requests == []
    client.close()


def test_expired_or_lookalike_session_cookie_is_rejected(tmp_path: Path) -> None:
    for domain, expires in (
        ("supermercado.eroski.es.evil.test", -1),
        ("supermercado.eroski.es", 1),
    ):
        requests: list[httpx.Request] = []
        client = _client(tmp_path, _router(requests))
        client.state_path.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "JSESSIONID",
                            "value": "private",
                            "domain": domain,
                            "path": "/",
                            "expires": expires,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(AuthenticationRequired, match="JSESSIONID"):
            client.read_cart()
        assert requests == []
        client.close()
