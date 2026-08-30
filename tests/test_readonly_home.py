"""Test that Open Grocery MCP can be imported in read-only environments.

This validates the fix for Vercel FUNCTION_INVOCATION_FAILED where HOME
is read-only and mkdir() would crash the import.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def test_import_server_with_readonly_home():
    """Importing server.py must not crash when HOME is read-only."""
    
    # Create a temporary read-only directory to simulate Vercel
    with tempfile.TemporaryDirectory() as tmpdir:
        readonly_home = Path(tmpdir) / "readonly_home"
        readonly_home.mkdir()
        readonly_home.chmod(0o555)  # read + execute only
        
        # Point HOME to the read-only directory
        original_home = os.environ.get("HOME")
        os.environ["HOME"] = str(readonly_home)
        
        try:
            # Clear any cached imports
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp")
            ]
            for name in modules_to_clear:
                del sys.modules[name]
            
            # This should not raise PermissionError or OSError
            from open_grocery_mcp import server
            
            # Verify the module loaded
            assert server is not None
            assert hasattr(server, "mcp")
            
        finally:
            # Restore original HOME
            if original_home is not None:
                os.environ["HOME"] = original_home
            else:
                os.environ.pop("HOME", None)
            
            # Clean up cached imports for next test
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp")
            ]
            for name in modules_to_clear:
                del sys.modules[name]


def test_import_server_with_vercel_env():
    """Importing server.py must work when VERCEL=1 is set."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        readonly_home = Path(tmpdir) / "readonly_home"
        readonly_home.mkdir()
        readonly_home.chmod(0o555)
        
        original_home = os.environ.get("HOME")
        original_vercel = os.environ.get("VERCEL")
        
        os.environ["HOME"] = str(readonly_home)
        os.environ["VERCEL"] = "1"
        
        try:
            # Clear cached imports
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp")
            ]
            for name in modules_to_clear:
                del sys.modules[name]
            
            # Should use /tmp/open-grocery-mcp instead of HOME
            from open_grocery_mcp.state_dir import get_state_dir
            
            state_dir = get_state_dir()
            assert str(state_dir) == "/tmp/open-grocery-mcp"
            
            # Server import should work
            from open_grocery_mcp import server
            assert server is not None
            
        finally:
            if original_home is not None:
                os.environ["HOME"] = original_home
            else:
                os.environ.pop("HOME", None)
            
            if original_vercel is not None:
                os.environ["VERCEL"] = original_vercel
            else:
                os.environ.pop("VERCEL", None)
            
            # Clean up
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp")
            ]
            for name in modules_to_clear:
                del sys.modules[name]


def test_shared_addresses_with_readonly_home():
    """Shared addresses module must not crash on import with read-only HOME."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        readonly_home = Path(tmpdir) / "readonly_home"
        readonly_home.mkdir()
        readonly_home.chmod(0o555)
        
        original_home = os.environ.get("HOME")
        os.environ["HOME"] = str(readonly_home)
        
        try:
            # Clear cached imports
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp")
            ]
            for name in modules_to_clear:
                del sys.modules[name]
            
            # Import should not crash
            from open_grocery_mcp import shared_addresses
            
            assert shared_addresses is not None
            
            # Reading addresses should work (returns empty list)
            result = shared_addresses.list_shared_addresses()
            assert result["count"] == 0
            assert result["addresses"] == []
            
        finally:
            if original_home is not None:
                os.environ["HOME"] = original_home
            else:
                os.environ.pop("HOME", None)
            
            # Clean up
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp")
            ]
            for name in modules_to_clear:
                del sys.modules[name]


def test_browser_account_state_with_readonly_home():
    """Browser account state must not crash with read-only HOME."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        readonly_home = Path(tmpdir) / "readonly_home"
        readonly_home.mkdir()
        readonly_home.chmod(0o555)
        
        original_home = os.environ.get("HOME")
        os.environ["HOME"] = str(readonly_home)
        
        try:
            # Clear cached imports
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp")
            ]
            for name in modules_to_clear:
                del sys.modules[name]
            
            from open_grocery_mcp.providers.browser_account_state import (
                default_state_root,
            )
            
            # Should not crash during import/construction
            state_root = default_state_root()
            assert state_root is not None
            # The path itself may point to HOME (which is read-only),
            # but the important thing is that getting the path doesn't crash.
            # Writing will be handled by ensure_state_dir() which will fall back to /tmp.
            
        finally:
            if original_home is not None:
                os.environ["HOME"] = original_home
            else:
                os.environ.pop("HOME", None)
            
            # Clean up
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp")
            ]
            for name in modules_to_clear:
                del sys.modules[name]


def test_vercel_asgi_app_construction():
    """The ASGI app for Vercel must construct without errors in read-only HOME."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        readonly_home = Path(tmpdir) / "readonly_home"
        readonly_home.mkdir()
        readonly_home.chmod(0o555)
        
        original_home = os.environ.get("HOME")
        original_vercel = os.environ.get("VERCEL")
        
        os.environ["HOME"] = str(readonly_home)
        os.environ["VERCEL"] = "1"
        
        try:
            # Clear cached imports
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp") or name == "api.index"
            ]
            for name in modules_to_clear:
                del sys.modules[name]
            
            # Add api to path if needed
            workspace_root = Path(__file__).resolve().parents[1]
            if str(workspace_root) not in sys.path:
                sys.path.insert(0, str(workspace_root))
            
            # This is what Vercel runs - should not crash
            from api.index import app
            
            assert app is not None
            
        finally:
            if original_home is not None:
                os.environ["HOME"] = original_home
            else:
                os.environ.pop("HOME", None)
            
            if original_vercel is not None:
                os.environ["VERCEL"] = original_vercel
            else:
                os.environ.pop("VERCEL", None)
            
            # Clean up
            modules_to_clear = [
                name for name in sys.modules
                if name.startswith("open_grocery_mcp") or name == "api.index"
            ]
            for name in modules_to_clear:
                del sys.modules[name]
