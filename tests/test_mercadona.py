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
