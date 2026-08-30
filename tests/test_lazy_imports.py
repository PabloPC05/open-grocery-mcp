"""Test that importing the server/registry does not eagerly import playwright."""

import sys


def test_server_import_does_not_load_playwright():
    """Verify that importing open_grocery_mcp.server does not import playwright.
    
    This ensures the slim Docker image without playwright can start without errors.
    """
    # Clear any previous imports
    playwright_modules = [k for k in sys.modules if "playwright" in k]
    for mod in playwright_modules:
        del sys.modules[mod]
    
    # Import the server
    from open_grocery_mcp import server  # noqa: F401
    
    # Verify playwright is not imported
    playwright_modules = [k for k in sys.modules if "playwright" in k]
    assert not playwright_modules, (
        f"Importing open_grocery_mcp.server loaded playwright modules: {playwright_modules}. "
        "Playwright should be lazy-imported only when browser providers are actually used."
    )


def test_registry_import_does_not_load_playwright():
    """Verify that importing open_grocery_mcp.registry does not import playwright.
    
    This ensures creating the default registry is safe without playwright installed.
    """
    # Clear any previous imports
    playwright_modules = [k for k in sys.modules if "playwright" in k]
    for mod in playwright_modules:
        del sys.modules[mod]
    
    # Import and create registry
    from open_grocery_mcp.registry import ProviderRegistry  # noqa: F401
    
    registry = ProviderRegistry()
    
    # Verify playwright is not imported
    playwright_modules = [k for k in sys.modules if "playwright" in k]
    assert not playwright_modules, (
        f"Creating ProviderRegistry loaded playwright modules: {playwright_modules}. "
        "Playwright should be lazy-imported only when browser providers are actually used."
    )


def test_getting_http_provider_does_not_load_playwright():
    """Verify that getting an HTTP-only provider does not import playwright."""
    # Clear any previous imports
    playwright_modules = [k for k in sys.modules if "playwright" in k]
    for mod in playwright_modules:
        del sys.modules[mod]
    
    from open_grocery_mcp.registry import ProviderRegistry
    
    registry = ProviderRegistry()
    
    # Get Mercadona provider (HTTP-only, no browser fallback in catalogue)
    provider = registry.get("mercadona")
    assert provider is not None
    
    # Verify playwright is not imported
    playwright_modules = [k for k in sys.modules if "playwright" in k]
    assert not playwright_modules, (
        f"Getting mercadona provider loaded playwright modules: {playwright_modules}. "
        "HTTP-only operations should not require playwright."
    )
