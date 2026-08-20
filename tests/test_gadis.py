import httpx

from open_grocery_mcp.providers.gadis import GadisProvider


def test_gadis_search_bootstraps_store_and_normalizes_product() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "site.gadisline.com":
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {"id": "site-1", "default_assortment_store": "store-7"}
                    ]
                },
            )
        assert request.headers["site-id"] == "site-1"
        assert request.headers["store-id"] == "store-7"
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "id": "p1",
                        "commercial_description": [
                            {"language": "ES", "value": "Leche entera 1 L"}
                        ],
                        "price": 1.05,
                        "price_kilo_litre": 1.05,
                        "price_kilo_litre_suffix": [
                            {"language": "ES", "value": "el litro"}
                        ],
                        "brand_description": "Marca",
                        "slug": "/producto/leche",
                        "properties": [],
                        "categories": [
                            {
                                "level": 2,
                                "name": [
                                    {"language": "ES", "value": "Leche"}
                                ],
                            }
                        ],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GadisProvider(client=client)
    result = provider.search("leche", limit=5)
    assert len(result) == 1
    assert result[0].name == "Leche entera 1 L"
    assert result[0].unit == "L"
    assert result[0].metadata["store_id"] == "store-7"
    assert len(requests) == 2
    client.close()


def test_gadis_product_extracts_detail_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "site.gadisline.com":
            return httpx.Response(
                200,
                json={"elements": [{"id": "s", "default_assortment_store": "x"}]},
            )
        return httpx.Response(
            200,
            json={
                "id": "p1",
                "commercial_description": "Tomate",
                "price": 2.0,
                "properties": [],
                "aecoc_properties": [
                    {
                        "code": "ORIGE",
                        "details": [{"language": "ES", "value": "Galicia"}],
                    },
                    {
                        "code": "INFIN",
                        "details": [
                            {"language": "ES", "value": "Tomate<br>Sal"}
                        ],
                    },
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    product = GadisProvider(client=client).product("p1")
    assert product.origin == "Galicia"
    assert product.ingredients == "Tomate\nSal"
    client.close()
