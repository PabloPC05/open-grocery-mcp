import json

import httpx
import pytest

from open_grocery_mcp.errors import LocationRequired
from open_grocery_mcp.providers.mercadona import MercadonaProvider


def test_mercadona_resolves_postal_warehouse_before_search() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/postal-codes/actions/change-pc/":
            assert json.loads(request.content)["new_postal_code"] == "28050"
            return httpx.Response(200, headers={"x-customer-wh": "mad1"}, json={})
        assert request.url.host == "7uzjkl1dj0-dsn.algolia.net"
        assert "/products_prod_mad1_es/" in request.url.path
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "id": "12",
                        "display_name": "Arroz redondo 1 kg",
                        "price_instructions": {
                            "unit_price": "1.35",
                            "reference_price": "1.35",
                            "reference_format": "kg",
                        },
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MercadonaProvider(client=client)
    products = provider.search("arroz", postal_code="28050")
    assert products[0].metadata["warehouse"] == "mad1"
    assert products[0].price_per_unit is not None
    # Warehouse resolution is cached for subsequent calls.
    provider.search("arroz", postal_code="28050")
    assert sum(r.url.path == "/api/postal-codes/actions/change-pc/" for r in seen) == 1
    client.close()


def test_mercadona_refuses_location_ambiguous_prices() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    provider = MercadonaProvider(client=client)
    with pytest.raises(LocationRequired):
        provider.search("leche")
    client.close()


def test_mercadona_search_page_exposes_exact_total_and_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["page"] == 0
        assert body["hitsPerPage"] == 2
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "id": "1",
                        "display_name": "Harina de trigo",
                        "price_instructions": {"unit_price": "1"},
                    }
                ],
                "nbHits": 3,
                "nbPages": 2,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MercadonaProvider(client=client, warehouse="mad1")

    page = provider.search_page("harina", page_size=2)

    assert page["total"] == 3
    assert page["has_next"] is True
    assert page["next_cursor"] == "1"
    client.close()


def test_mercadona_does_not_call_reference_price_a_promotion() -> None:
    provider = MercadonaProvider(warehouse="mad1")
    product = provider._product_from_raw(
        {
            "id": "12",
            "display_name": "Arroz",
            "price_instructions": {
                "unit_price": "1.35",
                "reference_price": "1.35",
                "reference_format": "kg",
            },
        },
        "mad1",
    )

    assert product.metadata["promotion"] == {
        "available": False,
        "current_price": 1.35,
        "previous_price": None,
        "offer_price": None,
        "source": "not_observed",
    }
    provider.close()


def test_mercadona_uses_explicit_promotion_fields_only() -> None:
    provider = MercadonaProvider(warehouse="mad1")
    product = provider._product_from_raw(
        {
            "id": "12",
            "display_name": "Arroz",
            "price_instructions": {
                "unit_price": "1.35",
                "reference_price": "1.00",
                "offer_price": "0.99",
                "previous_price": "1.50",
            },
        },
        "mad1",
    )

    promotion = product.metadata["promotion"]
    assert promotion["available"] is True
    assert promotion["current_price"] == 1.35
    assert promotion["offer_price"] == 0.99
    assert promotion["previous_price"] == 1.5
    assert promotion["source"] == "offer_price_field"
    provider.close()
