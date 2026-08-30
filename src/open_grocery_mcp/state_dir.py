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
    3. ~/.open-grocery-mcp otherwise
    
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
    
    # Use HOME-based directory for local persistence
    # The actual writability check happens in ensure_state_dir()
    try:
        return Path.home() / ".open-grocery-mcp"
    except (RuntimeError, OSError):
        # If Path.home() fails, fall back to /tmp
        return Path("/tmp/open-grocery-mcp")


def ensure_state_dir() -> Path | None:
    """Ensure the state directory exists and is writable.
    
    This should be called before attempting to write to the state directory.
    Unlike get_state_dir(), this actually creates the directory.
    
    If the directory from get_state_dir() is not writable (e.g., read-only HOME),
    this will try /tmp/open-grocery-mcp as a fallback.
    
    Returns:
        Path to the state directory, or None if creation failed
    """
    state_dir = get_state_dir()
    
    # Try to create the preferred directory
    try:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return state_dir
    except (OSError, PermissionError):
        # If it fails and it's not already /tmp, try /tmp as fallback
        tmp_fallback = Path("/tmp/open-grocery-mcp")
        if state_dir != tmp_fallback:
            try:
                tmp_fallback.mkdir(parents=True, exist_ok=True, mode=0o700)
                return tmp_fallback
            except (OSError, PermissionError):
                pass
        # Even /tmp failed; return None
        return None
