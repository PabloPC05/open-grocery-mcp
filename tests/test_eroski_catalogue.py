from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from open_grocery_mcp.errors import LocationRequired, ProviderError
from open_grocery_mcp.providers.eroski_catalogue import (
    EroskiCatalogueProvider,
    parse_products,
)

SEARCH_HTML = """
<div class="col product-item-lineal item-type-1">
  <div class="product-item big-item">
    <a class="product-title-link" href="/es/productdetail/123-leche-entera/">Leche entera</a>
    <img class="product-img" src="/images/123.jpg" alt="Leche entera"/>
    <span class="price-offer-now">1,09</span><span class="price-offer-now-euro">€</span>
  </div>
</div>
<div class="col product-item-lineal item-type-1">
  <div class="product-item big-item">
    <a class="product-title-link" href="/es/productdetail/456-leche-semi/">Leche semi</a>
    <img class="product-img" src="/images/456.jpg" alt="Leche semi"/>
    <span class="price-offer-now">0,95</span><span class="price-offer-now-euro">€</span>
  </div>
</div>
"""

PROMOTION_HTML = """
<div class="col product-item-lineal item-type-1">
  <div class="product-item big-item">
    <a class="product-title-link" href="/es/productdetail/789-arroz/">Arroz</a>
    <span class="price-offer-before">3,00 €</span>
    <span class="price-offer-now">2,00</span>
    <span class="offer-badge" data-offer-type="campaign">2x1</span>
  </div>
</div>
<div class="col product-item-lineal item-type-1">
  <div class="product-item big-item">
    <a class="product-title-link" href="/es/productdetail/790-leche/">Leche</a>
    <span class="price-offer-before">1,50 €</span>
    <span class="price-offer-now">1,20</span>
    <span class="promotion-label">2ª unidad</span>
  </div>
</div>
"""

SECOND_UNIT_HTML = """
<div class="col product-item-lineal item-type-1">
  <div class="product-item big-item">
    <a class="product-title-link" href="/es/productdetail/791-aceite/">Aceite</a>
    <span class="price-offer-now">6,79</span>
    <span class="promotion-label">2ª unidad -70 %</span>
  </div>
</div>
"""


def test_parse_public_search_cards() -> None:
    products = parse_products(SEARCH_HTML)

    assert [(item.id, item.name, item.price) for item in products] == [
        ("123", "Leche entera", Decimal("1.09")),
        ("456", "Leche semi", Decimal("0.95")),
    ]
    assert products[0].store == "eroski"
    assert products[0].url == (
        "https://supermercado.eroski.es/es/productdetail/123-leche-entera/"
    )
    assert products[0].metadata["image_url"] == (
        "https://supermercado.eroski.es/images/123.jpg"
    )
    assert "promotion" not in products[0].metadata


def test_parse_promotions_preserves_explicit_prices_labels_types_and_quantity() -> None:
    products = parse_products(PROMOTION_HTML)

    first = products[0].metadata["promotion"]
    assert first == {
        "current_price": 2.0,
        "previous_price": 3.0,
        "label": "2x1",
        "type": "campaign",
        "quantity_mechanic": {"buy_quantity": 2, "pay_quantity": 1},
    }
    second = products[1].metadata["promotion"]
    assert second["current_price"] == 1.2
    assert second["previous_price"] == 1.5
    assert second["label"] == "2ª unidad"
    assert second["type"] == "offer"
    assert second["quantity_mechanic"] == {"buy_quantity": 2}
    assert all("<" not in str(item.metadata) for item in products)


def test_parse_explicit_second_unit_percentage() -> None:
    product = parse_products(SECOND_UNIT_HTML)[0]

    assert product.metadata["promotion"]["quantity_mechanic"] == {
        "buy_quantity": 2,
        "discount_percent": 70.0,
    }


def test_search_reads_the_public_storefront_without_fake_location_context() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/es/search/results/":
            return httpx.Response(200, text=SEARCH_HTML)
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    provider = EroskiCatalogueProvider(client=http)

    products = provider.search("leche", limit=1)

    assert [item.id for item in products] == ["123"]
    assert len(requests) == 1
    assert requests[0].url.params["q"] == "leche"
    assert products[0].metadata["location_aware"] is False
    provider.close()
    http.close()


def test_search_validates_an_optional_spanish_postal_code() -> None:
    provider = EroskiCatalogueProvider(client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=SEARCH_HTML))))
    assert provider.search("leche", postal_code=None)
    with pytest.raises(LocationRequired):
        provider.search("leche", postal_code="48")
    provider.close()


def test_product_resolves_an_exact_id_through_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok" if request.url.path == "/" else SEARCH_HTML)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EroskiCatalogueProvider(client=http)

    product = provider.product("456", postal_code="48001")

    assert product.id == "456"
    assert "supermercado.eroski.es" in product.url
    provider.close()
    http.close()


def test_catalogue_discards_external_product_and_image_urls() -> None:
    hostile = SEARCH_HTML.replace(
        'href="/es/productdetail/123-leche-entera/"',
        'href="https://evil.test/es/productdetail/123-leche-entera/"',
    ).replace(
        'src="/images/456.jpg"',
        'src="https://images.evil.test/456.jpg"',
    )
    products = parse_products(hostile)
    assert [product.id for product in products] == ["456"]
    assert products[0].metadata["image_url"] is None


def test_catalogue_reports_login_or_antibot_challenge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='<form action="/es/login/"><input type="password"></form>')

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EroskiCatalogueProvider(client=http)
    with pytest.raises(ProviderError, match="challenge"):
        provider.search("leche", postal_code="48001")
    provider.close()
    http.close()


def test_catalogue_does_not_treat_normal_login_navigation_as_a_challenge() -> None:
    html = SEARCH_HTML + '<a href="/es/login/?l=1">Identifícate</a>'
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html)))
    provider = EroskiCatalogueProvider(client=http)
    assert provider.search("leche", limit=1)[0].id == "123"
    provider.close()
    http.close()


def test_catalogue_follows_redirects_and_rejects_untrusted_final_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "evil.test":
            return httpx.Response(200, text=SEARCH_HTML)
        return httpx.Response(302, headers={"location": "https://evil.test/"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EroskiCatalogueProvider(client=http)
    with pytest.raises(ProviderError, match="untrusted host"):
        provider.search("leche")
    provider.close()
    http.close()


def test_catalogue_follows_a_same_host_redirect() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                302,
                headers={"location": "/es/search/results/?q=leche&view=1"},
            )
        return httpx.Response(200, text=SEARCH_HTML)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EroskiCatalogueProvider(client=http)
    assert provider.search("leche", limit=1)[0].id == "123"
    assert calls == 2
    provider.close()
    http.close()


def test_eco_filter_is_not_silently_ignored() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok" if request.url.path == "/" else SEARCH_HTML)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EroskiCatalogueProvider(client=http)
    assert provider.search("leche", postal_code="48001", eco=True) == []
    provider.close()
    http.close()
