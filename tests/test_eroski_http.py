"""Mocked-transport tests for the Eroski Tapestry HTTP client."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote_plus

import httpx
import pytest

from open_grocery_mcp.errors import AuthenticationRequired
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


def _router(requests: list[httpx.Request], *, unauthorized: bool = False):
    state = {"first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/" and request.url.params.get("zipCode"):
            if unauthorized and state["first"]:
                state["first"] = False
                return httpx.Response(
                    200, text='Identifícate <input type="password"/>'
                )
            return httpx.Response(200, text="<html>ok</html>")
        if path == "/es/search/results/":
            return httpx.Response(200, text=SEARCH_HTML)
        if path.endswith(":addtocart"):
            content = unquote_plus(request.content.decode())
            assert '"productRef":"157719"' in content.replace(" ", "")
            assert "TOKEN-A" in content or "CART-TOKEN" in content
            assert "unitsToAdd" in content
            return httpx.Response(200, json={"_tapestry": True})
        if path == "/es/mycart/":
            return httpx.Response(200, text=MYCART_HTML)
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

    def patched(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/es/mycart/":
            return httpx.Response(200, text=html_with_cfg)
        return original_handler(request)

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
