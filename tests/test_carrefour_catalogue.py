"""Tests for Carrefour catalogue provider."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from open_grocery_mcp.errors import LocationRequired, ProviderError
from open_grocery_mcp.providers.carrefour_catalogue import CarrefourCatalogueProvider


@pytest.fixture
def mock_empathy_response():
    """Mock Empathy API search response."""
    return {
        "results": [
            {
                "__id": "12345",
                "__name": "Leche Entera Carrefour 1L",
                "__price": {
                    "value": 1.25,
                    "unit": {"value": 1.25}
                },
                "__url": "/supermercado/leche-entera-carrefour-1l/12345",
                "__images": ["https://www.carrefour.es/images/12345.jpg"],
                "__brand": "Carrefour",
                "__ean": "8414662002945",
                "__available": True,
            },
            {
                "__id": "67890",
                "__name": "Leche Semidesnatada Puleva 1L",
                "__price": {
                    "value": 1.45,
                    "referencePrice": {"value": 1.45}
                },
                "__url": "/supermercado/leche-semidesnatada-puleva-1l/67890",
                "__images": ["https://www.carrefour.es/images/67890.jpg"],
                "__brand": "Puleva",
                "__available": True,
            },
        ],
        "totalResults": 2,
        "facets": [],
    }


@pytest.fixture
def mock_cloudflare_403_html():
    """Mock Cloudflare 403 response HTML."""
    return """<!DOCTYPE html>
<html>
<head><title>Attention Required! | Cloudflare</title></head>
<body>
<h1>Sorry, you have been blocked</h1>
<p>This website is using a security service to protect itself from online attacks.</p>
</body>
</html>"""


@pytest.fixture
def mock_challenge_html():
    """Mock challenge/captcha HTML."""
    return """<!DOCTYPE html>
<html>
<head><title>Just a moment...</title></head>
<body>
<div id="challenge-running">Checking your browser...</div>
<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>
</body>
</html>"""


def test_search_happy_path(mock_empathy_response):
    """Test successful product search."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/search-api/query/v1/search" in str(request.url)
        assert "query=leche" in str(request.url)
        assert "instance=x-carrefour" in str(request.url)
        
        return httpx.Response(
            200,
            json=mock_empathy_response,
            headers={"content-type": "application/json"},
        )
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    products = provider.search("leche", limit=10)
    
    assert len(products) == 2
    
    product1 = products[0]
    assert product1.store == "carrefour"
    assert product1.id == "12345"
    assert product1.name == "Leche Entera Carrefour 1L"
    assert product1.price == Decimal("1.25")
    assert product1.currency == "EUR"
    assert product1.brand == "Carrefour"
    assert product1.ean == "8414662002945"
    assert product1.available is True
    assert "carrefour.es" in product1.url
    
    product2 = products[1]
    assert product2.id == "67890"
    assert product2.brand == "Puleva"


def test_search_empty_query():
    """Test empty query returns empty list."""
    provider = CarrefourCatalogueProvider(client=httpx.Client())
    products = provider.search("")
    assert products == []


def test_search_http_403_without_vercel(mock_empathy_response, monkeypatch):
    """Test HTTP 403 raises ProviderError with anti-bot message."""
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    
    call_count = 0
    
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        
        # First call 403, second call (with cookies) succeeds
        if call_count == 1:
            return httpx.Response(403, text="Forbidden")
        else:
            return httpx.Response(
                200,
                json=mock_empathy_response,
                headers={"content-type": "application/json"},
            )
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    # Should retry with cookies and succeed
    products = provider.search("leche")
    assert len(products) == 2
    assert call_count == 2


def test_search_http_403_on_vercel(monkeypatch):
    """Test HTTP 403 on Vercel immediately raises without retry."""
    monkeypatch.setenv("VERCEL", "1")
    
    call_count = 0
    
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(403, text="Forbidden")
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    with pytest.raises(ProviderError) as exc_info:
        provider.search("leche")
    
    assert call_count == 1  # No retry on Vercel
    assert "Cloudflare" in str(exc_info.value)
    assert "403" in str(exc_info.value)
    assert "Hosted MCP" in str(exc_info.value)


def test_search_http_429_on_vercel_no_retry(monkeypatch):
    """Test HTTP 429 on Vercel immediately raises without retry."""
    monkeypatch.setenv("VERCEL", "1")
    
    call_count = 0
    
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, text="Too Many Requests")
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    with pytest.raises(ProviderError) as exc_info:
        provider.search("leche")
    
    assert call_count == 1  # No retry on Vercel for 429
    assert "429" in str(exc_info.value)


def test_search_http_429():
    """Test HTTP 429 (rate limit) raises ProviderError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too Many Requests")
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    with pytest.raises(ProviderError) as exc_info:
        provider.search("leche")
    
    assert "429" in str(exc_info.value)


def test_search_http_503():
    """Test HTTP 503 (service unavailable) raises ProviderError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    with pytest.raises(ProviderError) as exc_info:
        provider.search("leche")
    
    assert "503" in str(exc_info.value)


def test_search_challenge_html_detected(mock_challenge_html):
    """Test Cloudflare challenge HTML is detected and raises ProviderError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=mock_challenge_html,
            headers={"content-type": "text/html"},
        )
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    with pytest.raises(ProviderError) as exc_info:
        provider.search("leche")
    
    assert "anti-bot challenge" in str(exc_info.value)


def test_search_captcha_html_detected(mock_cloudflare_403_html):
    """Test Cloudflare captcha HTML is detected and raises ProviderError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=mock_cloudflare_403_html,
            headers={"content-type": "text/html"},
        )
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    with pytest.raises(ProviderError) as exc_info:
        provider.search("leche")
    
    assert "anti-bot challenge" in str(exc_info.value)


def test_search_invalid_json():
    """Test invalid JSON response raises ProviderError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="not json",
            headers={"content-type": "application/json"},
        )
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    with pytest.raises(ProviderError) as exc_info:
        provider.search("leche")
    
    assert "invalid JSON" in str(exc_info.value)


def test_search_filters_invalid_products():
    """Test products without ID/name/price are filtered out."""
    response = {
        "results": [
            {"__id": "", "__name": "Product", "__price": {"value": 1.0}},
            {"__id": "123", "__name": "", "__price": {"value": 1.0}},
            {"__id": "456", "__name": "Valid", "__price": {"value": 0}},
            {"__id": "789", "__name": "Valid Product", "__price": {"value": 2.5}},
        ],
        "totalResults": 4,
    }
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    products = provider.search("test")
    
    # Only the last product is valid
    assert len(products) == 1
    assert products[0].id == "789"


def test_search_eco_filter(mock_empathy_response):
    """Test eco filter works."""
    # Add an eco product
    mock_empathy_response["results"].append({
        "__id": "111",
        "__name": "Leche Ecológica Bio 1L",
        "__price": {"value": 2.5},
        "__available": True,
    })
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_empathy_response)
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    products = provider.search("leche", eco=True)
    
    # Only the eco product should be returned
    assert len(products) == 1
    assert "Ecológica" in products[0].name or "Bio" in products[0].name


def test_postal_code_validation():
    """Test postal code validation."""
    provider = CarrefourCatalogueProvider(client=httpx.Client())
    
    # Valid postal codes
    assert provider._postal_code("28001") == "28001"
    assert provider._postal_code("08001") == "08001"
    
    # Invalid postal codes
    with pytest.raises(LocationRequired):
        provider._postal_code("2800")  # Too short
    
    with pytest.raises(LocationRequired):
        provider._postal_code("280012")  # Too long
    
    with pytest.raises(LocationRequired):
        provider._postal_code("abc12")  # Not numeric


def test_product_by_id(mock_empathy_response):
    """Test getting product by ID."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_empathy_response)
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    product = provider.product("12345")
    
    assert product.id == "12345"
    assert product.name == "Leche Entera Carrefour 1L"


def test_product_by_id_not_found():
    """Test product not found raises ProviderError."""
    response = {
        "results": [
            {"__id": "999", "__name": "Other Product", "__price": {"value": 1.0}}
        ],
        "totalResults": 1,
    }
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    with pytest.raises(ProviderError) as exc_info:
        provider.product("12345")
    
    assert "not found" in str(exc_info.value)


def test_catalogue_contract():
    """Test catalogue contract returns expected structure."""
    provider = CarrefourCatalogueProvider(client=httpx.Client())
    contract = provider.catalogue_contract()
    
    assert contract["pagination"] == "empathy_search"
    assert contract["maximum_page_size"] == 100
    assert contract["local_session_required"] is True


def test_url_sanitization():
    """Test product URLs are validated and sanitized."""
    response = {
        "results": [
            {
                "__id": "1",
                "__name": "Product 1",
                "__price": {"value": 1.0},
                "__url": "/supermercado/product-1/1",
            },
            {
                "__id": "2",
                "__name": "Product 2",
                "__price": {"value": 1.0},
                "__url": "https://evil.com/phishing",  # Invalid domain
            },
        ],
        "totalResults": 2,
    }
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    products = provider.search("test")
    
    assert "carrefour.es" in products[0].url
    assert products[1].url is None  # Invalid domain filtered out


def test_search_catalog_content_structure():
    """Test parsing Empathy catalog.content structure."""
    response = {
        "catalog": {
            "content": [
                {
                    "__id": "111",
                    "__name": "Product from catalog.content",
                    "__price": {"value": 2.5},
                    "__available": True,
                }
            ]
        }
    }
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    products = provider.search("test")
    
    assert len(products) == 1
    assert products[0].id == "111"
    assert products[0].name == "Product from catalog.content"


def test_search_catalog_content_docs_structure():
    """Test parsing Empathy catalog.content.docs structure."""
    response = {
        "catalog": {
            "content": {
                "docs": [
                    {
                        "__id": "222",
                        "__name": "Product from catalog.content.docs",
                        "__price": {"value": 3.5},
                        "__available": True,
                    }
                ]
            }
        }
    }
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    products = provider.search("test")
    
    assert len(products) == 1
    assert products[0].id == "222"
    assert products[0].name == "Product from catalog.content.docs"


def test_search_content_docs_structure():
    """Test parsing Empathy content.docs structure."""
    response = {
        "content": {
            "docs": [
                {
                    "__id": "333",
                    "__name": "Product from content.docs",
                    "__price": {"value": 4.5},
                    "__available": True,
                }
            ]
        }
    }
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)
    
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CarrefourCatalogueProvider(client=client)
    
    products = provider.search("test")
    
    assert len(products) == 1
    assert products[0].id == "333"
    assert products[0].name == "Product from content.docs"
