"""Test that server can be imported without Playwright installed.

This test ensures the Docker deployment (without [browser] extras) can
successfully import the MCP server module without triggering Playwright imports.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def test_server_import_without_playwright():
    """Verify server module imports successfully without playwright installed."""
    # Block playwright imports to simulate Docker environment without [browser] extra
    playwright_mock = MagicMock()
    playwright_mock.sync_api = MagicMock()
    
    # Save original import state
    original_modules = {}
    playwright_modules = [
        'playwright',
        'playwright.sync_api',
    ]
    
    for module in playwright_modules:
        if module in sys.modules:
            original_modules[module] = sys.modules[module]
        # Block the import by setting to None
        sys.modules[module] = None
    
    try:
        # This should succeed even with playwright blocked
        from open_grocery_mcp import server
        
        # Verify the server module loaded
        assert hasattr(server, 'mcp')
        assert hasattr(server, 'health')
        
        # Verify health tool works without playwright
        health_result = server.health()
        assert 'version' in health_result
        assert 'name' in health_result
        assert health_result['name'] == 'open-grocery-mcp'
        
    finally:
        # Restore original module state
        for module in playwright_modules:
            if module in original_modules:
                sys.modules[module] = original_modules[module]
            elif module in sys.modules:
                del sys.modules[module]


def test_registry_init_without_playwright():
    """Verify registry can initialize providers without playwright installed."""
    # Block playwright imports
    original_modules = {}
    playwright_modules = [
        'playwright',
        'playwright.sync_api',
    ]
    
    for module in playwright_modules:
        if module in sys.modules:
            original_modules[module] = sys.modules[module]
        sys.modules[module] = None
    
    try:
        from open_grocery_mcp.registry import default_registry
        
        # Registry should initialize successfully
        registry = default_registry()
        
        # Verify basic registry functionality
        stores = registry.list_stores()
        assert len(stores) > 0
        assert any(s['id'] == 'mercadona' for s in stores)
        
    finally:
        # Restore original module state
        for module in playwright_modules:
            if module in original_modules:
                sys.modules[module] = original_modules[module]
            elif module in sys.modules:
                del sys.modules[module]


def test_playwright_only_imported_when_needed():
    """Verify playwright is only imported lazily when browser operations are called."""
    import sys
    
    # Save the initial set of loaded modules
    initial_modules = set(sys.modules.keys())
    
    # Import server module
    from open_grocery_mcp import server
    
    # Check loaded modules after server import
    after_server_modules = set(sys.modules.keys())
    new_modules = after_server_modules - initial_modules
    
    # Playwright should NOT be imported just by loading the server
    playwright_imported = any('playwright' in mod for mod in new_modules)
    assert not playwright_imported, (
        f"Playwright was imported during server initialization. "
        f"New modules: {[m for m in new_modules if 'playwright' in m]}"
    )
