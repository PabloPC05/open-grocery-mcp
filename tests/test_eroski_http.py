"""Mocked-transport tests for the Eroski Tapestry HTTP client."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from open_grocery_mcp.errors import AuthenticationRequired
from open_grocery_mcp.providers.eroski_http import (
    EroskiCart,
    EroskiHTTPClient,
)

SEARCH_HTML = """
<form action="/es/search/results.productlist.productlistitem_1.productlistadditem:addtocart" method="post">
  <input type="hidden" name="q" value="leche"/>
  <input type="hidden" name="t:formdata" value="TOKEN-A"/>
</form>
"""

MYCART_HTML = """
<span class="shopping-cart__totalprice"><span class="price">1,65€</span></span>
<form action="/es/mycart.basket.productlist.basketproduct.basketadditemcomponent:addtocart" method="post">
  <input type="hidden" name="t:formdata" value="MYCART-TOKEN"/>
</form>
<div class="row shopping-cart-item">
  <div class="product-image basket-product-735399"></div>
  <input type="text" class="form-control quantity" value="1"/>
  <a class="remove-item-shopping-btn-cart">Eliminar item</a>
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
                return httpx.Response(200, text='Identifícate <input type="password"/>')
            return httpx.Response(200, text="<html>ok</html>")
        if path == "/es/search/results/":
            return httpx.Response(200, text=SEARCH_HTML)
        if path.startswith("/es/search/results.productlist"):
            content = request.content.decode()
            assert "q=leche" in content
            assert "TOKEN-A" in content
            return httpx.Response(301)
        if path == "/es/mycart/":
            return httpx.Response(200, text=MYCART_HTML)
        if "basketadditemcomponent" in path:
            body = request.content.decode()
            assert "product=735399" in body
            return httpx.Response(200, text="<html>ok</html>")
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
    assert cart.items[0].product_id == "735399"
    assert cart.items[0].quantity == 1
    assert cart.total_text == "1,65€"
    expected_version = cart.version
    assert cart.version == expected_version


def test_parse_add_forms_extracts_token(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    forms = client.search_add_forms("leche")
    assert forms and forms[0]["t_formdata"] == "TOKEN-A"
    assert "productlistadditem:addtocart" in forms[0]["action"]
    client.close()


def test_add_then_read_round_trip(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    cart = client.add_to_cart("leche", tile_index=0)
    assert cart.total_text == "1,65€"
    add_posts = [r for r in requests if r.method == "POST"]
    assert add_posts and "productlistadditem:addtocart" in add_posts[-1].url.path
    urls = [str(r.url) for r in requests]
    assert all("/orders" not in u for u in urls)
    assert all("/api/payment" not in u for u in urls)
    client.close()


def test_remove_item_posts_product_id(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests))
    cart = client.remove_item("735399")
    assert len(cart.items) == 1
    component_post = next(
        r for r in requests if "basketadditemcomponent" in r.url.path
    )
    body = component_post.content.decode()
    assert "product=735399" in body
    client.close()


def test_unauthenticated_context_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = _client(tmp_path, _router(requests, unauthorized=True))
    with pytest.raises(AuthenticationRequired):
        client.read_cart()
    client.close()
