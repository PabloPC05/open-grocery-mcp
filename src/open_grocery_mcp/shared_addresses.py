"""Shared postal addresses for all supermarkets.

The MCP persists a shared address book locally so users enter delivery
information once instead of per-store. These addresses are used for:
- Catalogue localization (postal code)
- Delivery coverage checks
- Basket comparison across stores
- Local authenticated delivery selection (when provider supports it)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.state_dir import get_state_dir, ensure_state_dir


# Factory default postal code (A Coruña, Spain)
# Used as last fallback when no other postal code is configured
FACTORY_DEFAULT_POSTAL_CODE = "15001"

PostalCodeSource = Literal["argument", "shared_default", "env", "builtin"]


def _addresses_file() -> Path:
    """Return the path to the shared addresses JSON file."""
    return get_state_dir() / "shared_addresses.json"


def _load_addresses() -> dict[str, Any]:
    """Load the shared addresses file or return empty structure."""
    addresses_file = _addresses_file()
    if not addresses_file.exists():
        return {"addresses": [], "default_id": None}
    
    try:
        with addresses_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Validate structure
        if not isinstance(data, dict):
            return {"addresses": [], "default_id": None}
        if "addresses" not in data:
            data["addresses"] = []
        if "default_id" not in data:
            data["default_id"] = None
        return data
    except (json.JSONDecodeError, OSError):
        return {"addresses": [], "default_id": None}


def _save_addresses(data: dict[str, Any]) -> None:
    """Save the shared addresses to disk."""
    state_dir = ensure_state_dir()
    if state_dir is None:
        raise InvalidRequest("Cannot save addresses: state directory is not writable")
    
    addresses_file = _addresses_file()
    with addresses_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    try:
        addresses_file.chmod(0o600)
    except (OSError, PermissionError):
        pass


def add_postal_address(
    *,
    label: str | None = None,
    street: str | None = None,
    number: str | None = None,
    postal_code: str,
    city: str | None = None,
    province: str | None = None,
    country: str = "ES",
    set_as_default: bool = True,
) -> dict[str, Any]:
    """Add a shared postal address for all supermarkets.
    
    The address is persisted locally and used as default for catalogue
    localization, coverage checks, basket comparison, and delivery selection.
    
    Args:
        label: Optional label (e.g. "Home", "Work")
        street: Street name
        number: Street number
        postal_code: Postal code (required)
        city: City name
        province: Province/region
        country: Country code (default: ES for Spain)
        set_as_default: Make this the default address
    
    Returns:
        Dict with the created address including its ID
    """
    if not postal_code or not isinstance(postal_code, str) or not postal_code.strip():
        raise InvalidRequest("postal_code is required and must be non-empty")
    
    data = _load_addresses()
    
    # Generate ID (simple incrementing counter)
    existing_ids = [addr.get("id", 0) for addr in data["addresses"]]
    new_id = max(existing_ids, default=0) + 1
    
    address = {
        "id": new_id,
        "label": label or f"Address {new_id}",
        "street": street,
        "number": number,
        "postal_code": postal_code.strip(),
        "city": city,
        "province": province,
        "country": country,
    }
    
    data["addresses"].append(address)
    
    if set_as_default or data["default_id"] is None:
        data["default_id"] = new_id
    
    _save_addresses(data)
    
    return {
        "address": address,
        "is_default": data["default_id"] == new_id,
        "message": "Address added successfully"
    }


def list_shared_addresses() -> dict[str, Any]:
    """List all shared postal addresses.
    
    Returns:
        Dict with addresses list and default_id
    """
    data = _load_addresses()
    
    # Mark the default address
    default_id = data.get("default_id")
    addresses_with_default = []
    for addr in data["addresses"]:
        addr_copy = dict(addr)
        addr_copy["is_default"] = addr.get("id") == default_id
        addresses_with_default.append(addr_copy)
    
    return {
        "addresses": addresses_with_default,
        "count": len(addresses_with_default),
        "default_id": default_id,
    }


def get_default_address() -> dict[str, Any] | None:
    """Get the default shared address, or None if none set.
    
    Returns:
        Address dict or None
    """
    data = _load_addresses()
    default_id = data.get("default_id")
    
    if default_id is None:
        return None
    
    for addr in data["addresses"]:
        if addr.get("id") == default_id:
            return dict(addr)
    
    return None


def set_default_address(address_id: int) -> dict[str, Any]:
    """Set an existing address as the default.
    
    Args:
        address_id: ID of the address to set as default
    
    Returns:
        Dict confirming the change
    """
    data = _load_addresses()
    
    # Check address exists
    found = False
    for addr in data["addresses"]:
        if addr.get("id") == address_id:
            found = True
            break
    
    if not found:
        raise InvalidRequest(f"Address with ID {address_id} not found")
    
    data["default_id"] = address_id
    _save_addresses(data)
    
    return {
        "address_id": address_id,
        "message": "Default address updated successfully"
    }


def remove_postal_address(address_id: int) -> dict[str, Any]:
    """Remove a shared postal address.
    
    Args:
        address_id: ID of the address to remove
    
    Returns:
        Dict confirming the removal
    """
    data = _load_addresses()
    
    # Find and remove the address
    original_count = len(data["addresses"])
    data["addresses"] = [addr for addr in data["addresses"] if addr.get("id") != address_id]
    
    if len(data["addresses"]) == original_count:
        raise InvalidRequest(f"Address with ID {address_id} not found")
    
    # If we removed the default, clear default_id
    if data.get("default_id") == address_id:
        # Set the first remaining address as default if any exist
        if data["addresses"]:
            data["default_id"] = data["addresses"][0].get("id")
        else:
            data["default_id"] = None
    
    _save_addresses(data)
    
    return {
        "address_id": address_id,
        "message": "Address removed successfully",
        "new_default_id": data.get("default_id"),
    }


def get_default_postal_code() -> str:
    """Get the postal code from the default address or the factory default.
    
    Returns:
        Postal code string (never None; falls back to factory default 15001)
    """
    postal_code, _ = resolve_postal_code(None)
    return postal_code


def validate_spanish_postal_code(postal_code: str) -> bool:
    """Validate Spanish postal code format (5 digits).
    
    Args:
        postal_code: Postal code to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not postal_code or not isinstance(postal_code, str):
        return False
    return bool(re.match(r"^\d{5}$", postal_code.strip()))


def resolve_postal_code(
    explicit: str | None,
) -> tuple[str, PostalCodeSource]:
    """Resolve postal code from multiple sources with priority order.
    
    Resolution order:
    1. Explicit postal_code argument (caller wins)
    2. Default shared address postal_code from get_default_address()
    3. Environment variable OPEN_GROCERY_DEFAULT_POSTAL_CODE
    4. Built-in factory default: 15001 (A Coruña, Spain)
    
    Args:
        explicit: Explicitly provided postal code or None
        
    Returns:
        Tuple of (resolved_postal_code, source)
        where source is "argument", "shared_default", "env", or "builtin"
    """
    # 1. Explicit argument wins
    if explicit is not None and isinstance(explicit, str) and explicit.strip():
        return explicit.strip(), "argument"
    
    # 2. Default shared address
    default_addr = get_default_address()
    if default_addr:
        postal_code = default_addr.get("postal_code")
        if postal_code and isinstance(postal_code, str) and postal_code.strip():
            return postal_code.strip(), "shared_default"
    
    # 3. Environment variable
    env_postal_code = os.getenv("OPEN_GROCERY_DEFAULT_POSTAL_CODE", "").strip()
    if env_postal_code:
        return env_postal_code, "env"
    
    # 4. Built-in factory default
    return FACTORY_DEFAULT_POSTAL_CODE, "builtin"


def set_default_postal_code(
    postal_code: str,
    city: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Set a postal code as the default by upserting a shared address.
    
    This function performs an upsert: if a default address already exists,
    it updates that address in place. Otherwise, it creates a new address.
    This prevents duplicate addresses from piling up on repeated calls.
    
    Args:
        postal_code: Postal code (required, validated)
        city: City name (optional)
        label: Label for the address (defaults to "Default")
        
    Returns:
        Dict with the created/updated address and confirmation
    """
    if not postal_code or not isinstance(postal_code, str) or not postal_code.strip():
        raise InvalidRequest("postal_code is required and must be non-empty")
    
    # Validate Spanish postal code format
    if not validate_spanish_postal_code(postal_code):
        raise InvalidRequest(
            f"Invalid Spanish postal code format: {postal_code!r}. "
            "Expected 5 digits (e.g., '15001', '28001')"
        )
    
    postal_code = postal_code.strip()
    label = label or "Default"
    
    # Load existing addresses
    data = _load_addresses()
    existing_default_id = data.get("default_id")
    
    # If a default exists, update it in place
    if existing_default_id is not None:
        for addr in data["addresses"]:
            if addr.get("id") == existing_default_id:
                # Update the existing default address
                addr["postal_code"] = postal_code
                addr["city"] = city
                addr["label"] = label
                
                _save_addresses(data)
                
                return {
                    "address": dict(addr),
                    "is_default": True,
                    "message": "Default address updated successfully",
                }
    
    # No default exists, create a new one
    return add_postal_address(
        postal_code=postal_code,
        city=city,
        label=label,
        set_as_default=True,
    )
