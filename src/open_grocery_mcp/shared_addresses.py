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
from pathlib import Path
from typing import Any

from open_grocery_mcp.errors import InvalidRequest


def _state_dir() -> Path:
    """Return the shared MCP state directory."""
    base = Path.home() / ".open-grocery-mcp"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def _addresses_file() -> Path:
    """Return the path to the shared addresses JSON file."""
    return _state_dir() / "shared_addresses.json"


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
    addresses_file = _addresses_file()
    with addresses_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    addresses_file.chmod(0o600)


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


def get_default_postal_code() -> str | None:
    """Get the postal code from the default address, or None.
    
    Returns:
        Postal code string or None
    """
    default = get_default_address()
    if default:
        return default.get("postal_code")
    return None
