"""Tests for Día catalogue provider."""

import httpx
import pytest

from open_grocery_mcp.errors import LocationRequired, ProviderError
from open_grocery_mcp.providers.dia_catalogue import DiaCatalogueProvider, parse_products


# Sample HTML fixture from actual dia.es search
SAMPLE_SEARCH_HTML = """
<ul data-test-id="search-product-card-list" class="search-product-card-list">
<li data-test-id="search-product-card-list-item" class="search-product-card-list__item-container">
<div data-test-id="product-card" class="search-product-card" 
     brand="Día Láctea" brand_type="D" 
     l1_category_description="Huevos, leche y mantequilla"
     l2_category_description="Leche"
     object_id="504P6">
  <a href="/huevos-leche-y-mantequilla/leche/p/504P6">
    <h3>Leche semidesnatada Día Láctea pack 6 x 1 L</h3>
  </a>
  <div data-test-id="price-container">
    <span data-test-id="product-price">5,04 €

(0,84 €/LITRO)</span>
  </div>
</div>
</li>
<li data-test-id="search-product-card-list-item">
<div data-test-id="product-card" class="search-product-card"
     brand="Día Láctea" brand_type="D"
     l2_category_description="Leche"
     object_id="608P6">
  <a href="/huevos-leche-y-mantequilla/leche/p/608P6">
    <h4>Leche entera Día Láctea pack 6 x 1 L</h4>
  </a>
  <div data-test-id="price-info">
    <span>5,76 €

(0,96 €/L)</span>
  </div>
</div>
</li>
</ul>
"""


def test_parse_products():
    """Test HTML parsing extracts product data correctly."""
    products = parse_products(SAMPLE_SEARCH_HTML)
    
    assert len(products) == 2
    
    # First product
    p1 = products[0]
    assert p1.store == "dia"
    assert p1.id == "504P6"
    assert "Leche semidesnatada" in p1.name
    assert float(p1.price) == pytest.approx(5.04, rel=1e-2)
    assert p1.currency == "EUR"
    assert float(p1.price_per_unit) == pytest.approx(0.84, rel=1e-2)
    assert p1.unit == "L"
    assert p1.brand == "Día Láctea"
    assert p1.category == "Leche"
    assert p1.url == "https://www.dia.es/huevos-leche-y-mantequilla/leche/p/504P6"
    assert p1.available is True
    
    # Second product
    p2 = products[1]
    assert p2.id == "608P6"
    assert "entera" in p2.name
    assert float(p2.price) == pytest.approx(5.76, rel=1e-2)
    assert float(p2.price_per_unit) == pytest.approx(0.96, rel=1e-2)
    assert p2.unit == "L"


def test_parse_products_with_no_unit_price():
    """Test parsing when unit price is missing."""
    html = """
    <div data-test-id="product-card" object_id="12345" brand="TestBrand">
      <h3>Test Product</h3>
      <div><span>2,50 €</span></div>
      <a href="/test/p/12345"></a>
    </div>
    """
    products = parse_products(html)
    
    assert len(products) == 1
    assert float(products[0].price) == pytest.approx(2.50, rel=1e-2)
    assert products[0].price_per_unit is None
    assert products[0].unit is None


def test_parse_products_filters_invalid():
    """Test that products without required fields are filtered."""
    html = """
    <div data-test-id="product-card" object_id="">
      <h3>No ID</h3>
      <span>1,00 €</span>
    </div>
    <div data-test-id="product-card" object_id="123">
      <h3></h3>
      <span>invalid price</span>
    </div>
    <div data-test-id="product-card" object_id="456">
      <h3>Valid Product</h3>
      <span>3,99 €</span>
    </div>
    """
    products = parse_products(html)
    
    assert len(products) == 1
    assert products[0].id == "456"


def test_postal_code_validation():
    """Test postal code validation."""
    provider = DiaCatalogueProvider()
    
    # Valid codes
    assert provider._postal_code("15001") == "15001"
    assert provider._postal_code("28001") == "28001"
    
    # Invalid codes
    with pytest.raises(LocationRequired):
        provider._postal_code("1234")  # Too short
    
    with pytest.raises(LocationRequired):
        provider._postal_code("123456")  # Too long
    
    with pytest.raises(LocationRequired):
        provider._postal_code("abcde")  # Not digits
    
    with pytest.raises(LocationRequired):
        provider._postal_code("")  # Empty


def test_search_with_mock_client():
    """Test search with mocked HTTP client."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "/search" in str(request.url) and request.url.params.get("q") == "leche":
            return httpx.Response(200, text=SAMPLE_SEARCH_HTML)
        return httpx.Response(404)
    
    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    provider = DiaCatalogueProvider(client=mock_client)
    
    products = provider.search("leche", limit=10)
    
    assert len(products) == 2
    assert all(p.store == "dia" for p in products)
    assert products[0].id == "504P6"
    assert products[1].id == "608P6"
    
    provider.close()


def test_search_empty_query():
    """Test search with empty query returns empty list."""
    provider = DiaCatalogueProvider()
    assert provider.search("") == []
    assert provider.search("   ") == []
    provider.close()


def test_search_with_postal_code():
    """Test search validates postal code when provided."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_SEARCH_HTML)
    
    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    provider = DiaCatalogueProvider(client=mock_client)
    
    # Valid postal code - should work
    products = provider.search("leche", postal_code="15001")
    assert len(products) == 2
    
    # Invalid postal code - should raise
    with pytest.raises(LocationRequired):
        provider.search("leche", postal_code="invalid")
    
    provider.close()


def test_search_http_error():
    """Test search handles HTTP errors gracefully."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")
    
    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    provider = DiaCatalogueProvider(client=mock_client)
    
    with pytest.raises(ProviderError, match="HTTP 500"):
        provider.search("leche")
    
    provider.close()


def test_search_anti_bot_blocking():
    """Test search detects anti-bot blocking."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Access Denied")
    
    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    provider = DiaCatalogueProvider(client=mock_client)
    
    with pytest.raises(ProviderError, match="anti-bot"):
        provider.search("leche")
    
    provider.close()


def test_product_by_id():
    """Test fetching a single product by ID."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_SEARCH_HTML)
    
    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    provider = DiaCatalogueProvider(client=mock_client)
    
    product = provider.product("504P6")
    
    assert product.id == "504P6"
    assert "semidesnatada" in product.name
    assert float(product.price) == pytest.approx(5.04, rel=1e-2)
    
    provider.close()


def test_product_not_found():
    """Test product() raises when ID not found."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_SEARCH_HTML)
    
    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    provider = DiaCatalogueProvider(client=mock_client)
    
    with pytest.raises(ProviderError, match="not found"):
        provider.product("nonexistent")
    
    provider.close()


def test_catalogue_contract():
    """Test catalogue_contract returns expected metadata."""
    provider = DiaCatalogueProvider()
    contract = provider.catalogue_contract()
    
    assert contract["pagination"] == "server_rendered_html"
    assert contract["maximum_page_size"] == 100
    assert contract["exact_total"] is False
    assert contract["cache_safe"] is True
    
    provider.close()
