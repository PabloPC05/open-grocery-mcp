"""Tests for Carrefour full provider."""

from __future__ import annotations

import httpx

from open_grocery_mcp.providers.carrefour_full import CarrefourFullProvider


def test_provider_info():
    """Test provider info is correctly configured."""
    provider = CarrefourFullProvider()
    
    assert provider.info.key == "carrefour"
    assert provider.info.label == "Carrefour"
    assert provider.info.country == "ES"
    assert "es" in provider.info.languages
    assert "search" in provider.info.capabilities
    assert "product" in provider.info.capabilities
    assert provider.info.requires_postal_code is False


def test_search_delegates_to_catalogue():
    """Test search delegates to catalogue provider."""
    response = {
        "results": [
            {
                "__id": "123",
                "__name": "Test Product",
                "__price": {"value": 1.99},
                "__available": True,
            }
        ],
        "totalResults": 1,
    }
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)
    
    # Inject mock client via constructor
    from open_grocery_mcp.providers.carrefour_catalogue import CarrefourCatalogueProvider
    
    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    catalogue = CarrefourCatalogueProvider(client=mock_client)
    
    provider = CarrefourFullProvider()
    provider._catalogue = catalogue
    
    products = provider.search("test", limit=10)
    
    assert len(products) == 1
    assert products[0].id == "123"


def test_product_delegates_to_catalogue():
    """Test product() delegates to catalogue provider."""
    response = {
        "results": [
            {
                "__id": "123",
                "__name": "Test Product",
                "__price": {"value": 1.99},
                "__available": True,
            }
        ],
        "totalResults": 1,
    }
    
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)
    
    # Inject mock client via constructor
    from open_grocery_mcp.providers.carrefour_catalogue import CarrefourCatalogueProvider
    
    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    catalogue = CarrefourCatalogueProvider(client=mock_client)
    
    provider = CarrefourFullProvider()
    provider._catalogue = catalogue
    
    product = provider.product("123")
    
    assert product.id == "123"


def test_catalogue_contract():
    """Test catalogue_contract() returns expected structure."""
    provider = CarrefourFullProvider()
    contract = provider.catalogue_contract()
    
    assert "pagination" in contract
    assert "local_session_required" in contract


def test_close():
    """Test close() cleans up resources."""
    provider = CarrefourFullProvider()
    provider.close()  # Should not raise
