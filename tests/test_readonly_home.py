"""Test that Open Grocery MCP can be imported in read-only environments.

This validates the fix for Vercel FUNCTION_INVOCATION_FAILED where HOME
is read-only and mkdir() would crash the import.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_import_server_with_readonly_home():
    """Importing server.py must not crash when HOME is read-only."""
    
    # Run the test in a subprocess to avoid polluting sys.modules
    script = """
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmpdir:
    readonly_home = Path(tmpdir) / "readonly_home"
    readonly_home.mkdir()
    readonly_home.chmod(0o555)  # read + execute only
    
    os.environ["HOME"] = str(readonly_home)
    
    # This should not raise PermissionError or OSError
    from open_grocery_mcp import server
    
    # Verify the module loaded
    assert server is not None
    assert hasattr(server, "mcp")
    
    print("SUCCESS")
"""
    
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Import failed: {result.stderr}"
    assert "SUCCESS" in result.stdout


def test_import_server_with_vercel_env():
    """Importing server.py must work when VERCEL=1 is set."""
    
    script = """
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmpdir:
    readonly_home = Path(tmpdir) / "readonly_home"
    readonly_home.mkdir()
    readonly_home.chmod(0o555)
    
    os.environ["HOME"] = str(readonly_home)
    os.environ["VERCEL"] = "1"
    
    # Should use /tmp/open-grocery-mcp instead of HOME
    from open_grocery_mcp.state_dir import get_state_dir
    
    state_dir = get_state_dir()
    assert str(state_dir) == "/tmp/open-grocery-mcp"
    
    # Server import should work
    from open_grocery_mcp import server
    assert server is not None
    
    print("SUCCESS")
"""
    
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Import failed: {result.stderr}"
    assert "SUCCESS" in result.stdout


def test_shared_addresses_with_readonly_home():
    """Shared addresses module must not crash on import with read-only HOME."""
    
    script = """
import os
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmpdir:
    readonly_home = Path(tmpdir) / "readonly_home"
    readonly_home.mkdir()
    readonly_home.chmod(0o555)
    
    os.environ["HOME"] = str(readonly_home)
    
    # Import should not crash
    from open_grocery_mcp import shared_addresses
    
    assert shared_addresses is not None
    
    # Reading addresses should work (returns empty list)
    result = shared_addresses.list_shared_addresses()
    assert result["count"] == 0
    assert result["addresses"] == []
    
    print("SUCCESS")
"""
    
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Import failed: {result.stderr}"
    assert "SUCCESS" in result.stdout


def test_browser_account_state_with_readonly_home():
    """Browser account state must not crash with read-only HOME."""
    
    script = """
import os
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmpdir:
    readonly_home = Path(tmpdir) / "readonly_home"
    readonly_home.mkdir()
    readonly_home.chmod(0o555)
    
    os.environ["HOME"] = str(readonly_home)
    
    from open_grocery_mcp.providers.browser_account_state import default_state_root
    
    # Should not crash during import/construction
    state_root = default_state_root()
    assert state_root is not None
    # The path itself may point to HOME (which is read-only),
    # but the important thing is that getting the path doesn't crash.
    # Writing will be handled by ensure_state_dir() which will fall back to /tmp.
    
    print("SUCCESS")
"""
    
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Import failed: {result.stderr}"
    assert "SUCCESS" in result.stdout


def test_vercel_asgi_app_construction():
    """The ASGI app for Vercel must construct without errors in read-only HOME."""
    
    script = """
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmpdir:
    readonly_home = Path(tmpdir) / "readonly_home"
    readonly_home.mkdir()
    readonly_home.chmod(0o555)
    
    os.environ["HOME"] = str(readonly_home)
    os.environ["VERCEL"] = "1"
    
    # This is what Vercel runs - should not crash
    from api.index import app
    
    assert app is not None
    
    print("SUCCESS")
"""
    
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Import failed: {result.stderr}"
    assert "SUCCESS" in result.stdout
