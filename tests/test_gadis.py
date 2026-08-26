import json

import httpx
import pytest

from open_grocery_mcp.errors import CoverageError, LocationRequired
from open_grocery_mcp.providers.gadis import GadisProvider


def _product_payload() -> dict:
    return {
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
                        "name": [{"language": "ES", "value": "Leche"}],
                    }
                ],
            }
        ]
    }


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
        return httpx.Response(200, json=_product_payload())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GadisProvider(client=client)
    result = provider.search("leche", limit=5)
    assert len(result) == 1
    assert result[0].name == "Leche entera 1 L"
    assert result[0].unit == "L"
    assert result[0].metadata["store_id"] == "store-7"
    assert len(requests) == 2
    client.close()


def test_gadis_search_page_uses_total_only_when_schema_exposes_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "site.gadisline.com":
            return httpx.Response(
                200,
                json={"elements": [{"id": "site-1", "default_assortment_store": "store-7"}]},
            )
        payload = _product_payload()
        payload["total_elements"] = 3
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GadisProvider(client=client)

    page = provider.search_page("leche", page_size=1)

    assert page["total"] == 3
    assert page["has_next"] is True
    assert page["next_cursor"] == "1"
    client.close()


def test_gadis_promotion_metadata_marks_contract_without_offer() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    provider = GadisProvider(client=client)
    product = provider._product_from_raw(
        {
            "id": "p1",
            "commercial_description": [{"language": "es", "value": "Leche"}],
            "price": 1.05,
            "fidelity_offer_price": None,
            "offers": [],
        }
    )

    assert product.metadata["promotion"] == {
        "available": False,
        "current_price": 1.05,
        "previous_price": None,
        "offer_price": None,
        "source": "not_observed",
    }
    client.close()


def test_gadis_promotion_metadata_uses_explicit_fidelity_offer() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    provider = GadisProvider(client=client)
    product = provider._product_from_raw(
        {
            "id": "p1",
            "commercial_description": [{"language": "es", "value": "Leche"}],
            "price": 1.05,
            "fidelity_offer_price": 0.85,
        }
    )

    promotion = product.metadata["promotion"]
    assert promotion["available"] is True
    assert promotion["current_price"] == 1.05
    assert promotion["offer_price"] == 0.85
    assert promotion["previous_price"] is None
    assert promotion["source"] == "fidelity_offer_price"
    client.close()


def test_gadis_resolves_location_store_before_search_and_caches_coverage() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "site.gadisline.com":
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {"id": "site-1", "default_assortment_store": "default-store"}
                    ]
                },
            )
        if request.url.host == "store.gadisline.com":
            assert request.url.path == "/api/v3/stores/postal-codes/delivery"
            assert request.headers["site-id"] == "site-1"
            assert request.headers["store-id"] == "default-store"
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "postal_code": "28050",
                            "store_id": "madrid-store",
                            "shipping_costs": "4.90",
                            "minimum_order_quantity": "25",
                            "minimum_shipping_free": "90",
                        }
                    ]
                },
            )
        assert request.url.host == "catalog.gadisline.com"
        assert request.headers["site-id"] == "site-1"
        assert request.headers["store-id"] == "madrid-store"
        body = json.loads(request.content)
        assert body["search_term"] == "leche"
        return httpx.Response(200, json=_product_payload())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GadisProvider(client=client)
    first = provider.search("leche", postal_code="28050")
    second = provider.search("leche", postal_code="28050")
    assert first[0].metadata["store_id"] == "madrid-store"
    assert second[0].metadata["store_id"] == "madrid-store"
    coverage = provider.delivery_coverage("28050")
    assert coverage == {
        "store_id": "madrid-store",
        "postal_code": "28050",
        "shipping_costs": 4.9,
        "minimum_order_quantity": 25.0,
        "minimum_shipping_free": 90.0,
    }
    assert sum(r.url.host == "store.gadisline.com" for r in requests) == 1
    client.close()


def test_gadis_location_validation_and_uncovered_postal_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "site.gadisline.com":
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {"id": "site-1", "default_assortment_store": "default-store"}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "elements": [
                    {"postal_code": "15706", "store_id": "galicia-store"}
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GadisProvider(client=client)
    with pytest.raises(LocationRequired):
        provider.delivery_coverage("2805")
    with pytest.raises(CoverageError):
        provider.delivery_coverage("28050")
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


def test_gadis_exposes_public_leaflet_offer_without_inventing_discount() -> None:
    provider = GadisProvider(store_id="store-1")
    product = provider._product_from_raw(
        {
            "id": "p1",
            "commercial_description": "Leche entera 1 l",
            "price": 1.14,
            "properties": [],
            "offers": [
                {
                    "type": "DIPTYCH",
                    "end_date": "2999-08-26",
                    "is_offer_coupon": False,
                },
                {
                    "type": "COUPON",
                    "end_date": "2999-08-26",
                    "is_offer_coupon": True,
                },
            ],
        }
    )

    assert product.metadata["promotions"] == [
        {
            "type": "unknown",
            "description": "Gadis leaflet offer",
            "source": "offers.DIPTYCH",
            "ends_at": "2999-08-26",
        }
    ]
    provider.close()
