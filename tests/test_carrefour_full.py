"""Tests for Carrefour full provider."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from open_grocery_mcp.errors import InvalidRequest
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
    assert "login" in provider.info.capabilities
    assert "account" in provider.info.capabilities
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


def test_account_status():
    """Test account_status returns browser session info."""
    provider = CarrefourFullProvider()
    status = provider.account_status()
    
    assert isinstance(status, dict)
    assert "authenticated_session" in status
    assert status["store"] == "carrefour"


def test_import_invalid_storage_state(tmp_path: Path):
    """Test importing invalid storage_state is rejected."""
    provider = CarrefourFullProvider()
    
    # Create invalid storage state file
    invalid_state = tmp_path / "invalid_state.json"
    invalid_state.write_text("{}")
    
    with pytest.raises((InvalidRequest, ValueError)):
        provider.import_browser_session(str(invalid_state))


def test_import_valid_storage_state(tmp_path: Path):
    """Test importing valid storage_state copies it to state dir."""
    provider = CarrefourFullProvider()
    
    # Create valid storage state file with carrefour.es cookies
    valid_state = tmp_path / "valid_state.json"
    state_data = {
        "cookies": [
            {
                "name": "test_cookie",
                "value": "test_value",
                "domain": ".carrefour.es",
                "path": "/",
                "expires": 9999999999,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax"
            }
        ],
        "origins": []
    }
    valid_state.write_text(json.dumps(state_data))
    
    result = provider.import_browser_session(str(valid_state))
    
    assert isinstance(result, dict)
    # Result should include both import result and account status
    assert "authenticated_session" in result
    assert result["authenticated_session"] is True
    assert "cookie_count" in result


def test_clear_session():
    """Test clear_session removes stored browser session."""
    provider = CarrefourFullProvider()
    result = provider.clear_session()
    
    assert isinstance(result, dict)
