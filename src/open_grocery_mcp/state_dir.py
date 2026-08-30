"""Shared state directory management for Open Grocery MCP.

This module provides a centralized way to manage state directories that works
across different environments:
- Local development: uses ~/.open-grocery-mcp
- Vercel/serverless: uses /tmp/open-grocery-mcp (ephemeral but writable)
- Read-only environments: falls back to /tmp automatically
- Custom override: respects OPEN_GROCERY_STATE_DIR environment variable
"""

from __future__ import annotations

import os
from pathlib import Path


def get_state_dir() -> Path:
    """Return the state directory for Open Grocery MCP.
    
    Returns a writable directory in this order of preference:
    1. OPEN_GROCERY_STATE_DIR environment variable if set
    2. /tmp/open-grocery-mcp if VERCEL environment variable is set
    3. ~/.open-grocery-mcp if HOME is writable
    4. /tmp/open-grocery-mcp as fallback
    
    The directory is created lazily on first write, not during import.
    This function never raises; it returns a path that may not exist yet.
    
    Returns:
        Path to the state directory (may not exist yet)
    """
    # Check for explicit override
    if override := os.getenv("OPEN_GROCERY_STATE_DIR", "").strip():
        return Path(override)
    
    # On Vercel or similar serverless, use /tmp (ephemeral but writable)
    if os.getenv("VERCEL"):
        return Path("/tmp/open-grocery-mcp")
    
    # Try HOME-based directory for local persistence
    try:
        home_dir = Path.home() / ".open-grocery-mcp"
        # Check if HOME is writable by attempting to create parent if needed
        # This doesn't actually create the directory, just validates writability
        if home_dir.parent.exists() and os.access(home_dir.parent, os.W_OK):
            return home_dir
    except (RuntimeError, OSError, PermissionError):
        pass
    
    # Fallback to /tmp
    return Path("/tmp/open-grocery-mcp")


def ensure_state_dir() -> Path | None:
    """Ensure the state directory exists and is writable.
    
    This should be called before attempting to write to the state directory.
    Unlike get_state_dir(), this actually creates the directory.
    
    Returns:
        Path to the state directory, or None if creation failed
    """
    state_dir = get_state_dir()
    
    try:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return state_dir
    except (OSError, PermissionError):
        # Even /tmp might fail in extreme cases; return None
        return None
