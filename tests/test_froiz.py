from decimal import Decimal

import httpx

from open_grocery_mcp.providers.froiz import FroizProvider


def test_froiz_search_normalizes_empathy_result_and_removes_diacritics() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api.empathy.co"
        assert request.url.params["query"] == "pina"
        assert request.url.params["instance"] == "froiz"
        return httpx.Response(
            200,
            json={
                "catalog": {
                    "content": [
                        {
                            "id": "p1",
                            "slug": "pina-natural-p1",
                            "__name": "Piña natural 500 g",
                            "__prices": {"current": {"value": 2.5}},
                            "measurementUnit": "Kilogramo",
                            "measurementUnitRatio": 0.5,
                            "imageUrl": "https://img.example/pina.jpg",
                        }
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = FroizProvider(client=client)
    products = provider.search("piña", limit=5)
    assert len(products) == 1
    assert products[0].price == Decimal("2.5")
    assert products[0].price_per_unit == Decimal("5")
    assert products[0].unit == "kg"
    assert products[0].metadata["location_aware"] is False
    assert products[0].metadata["price_source"] == "empathy.__prices.current.value"
    assert products[0].metadata["catalogue_current_price"] == 2.5
    assert len(seen) == 1
    client.close()


def test_froiz_retries_a_shorter_query_when_strict_match_is_empty() -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        queries.append(query)
        if query == "pan molde integral 800 g":
            return httpx.Response(200, json={"catalog": {"content": []}})
        return httpx.Response(
            200,
            json={
                "catalog": {
                    "content": [
                        {
                            "id": "bread",
                            "__name": "Pan de molde integral 600 g",
                            "__prices": {"current": {"value": 1.75}},
                        }
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    products = FroizProvider(client=client).search("pan molde integral 800 g")
    assert products[0].id == "bread"
    assert queries[:2] == ["pan molde integral 800 g", "pan molde integral 800"]
    client.close()


def test_froiz_skips_malformed_products() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "catalog": {
                    "content": [
                        {"id": "missing-price", "__name": "Producto"},
                        {
                            "id": "ok",
                            "__name": "Producto válido",
                            "__prices": {"current": {"value": "1.20"}},
                        },
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    products = FroizProvider(client=client).search("producto")
    assert [product.id for product in products] == ["ok"]
    client.close()
