"""Map shared postal addresses to retailer delivery addresses."""

from __future__ import annotations

from typing import Any

from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.errors import ProviderError
from open_grocery_mcp.providers.base import GroceryProvider


def find_matching_address(
    retailer_addresses: list[dict[str, Any]],
    postal_code: str,
    street: str | None = None,
) -> dict[str, Any] | None:
    """Find a matching retailer address by postal code and optional street.
    
    Returns the best match or None if no match found.
    Addresses are typically redacted, so matching uses postal_code primarily.
    """
    if not retailer_addresses:
        return None
    
    postal_code_normalized = postal_code.strip().lower()
    
    # Try exact postal code match first
    candidates = []
    for addr in retailer_addresses:
        addr_postal = str(addr.get("postal_code", "")).strip().lower()
        if addr_postal == postal_code_normalized:
            candidates.append(addr)
    
    if not candidates:
        return None
    
    # If street provided, try to match it
    if street:
        street_normalized = street.strip().lower()
        for addr in candidates:
            addr_street = str(addr.get("street", "")).strip().lower()
            if street_normalized in addr_street or addr_street in street_normalized:
                return addr
    
    # Return first postal code match
    return candidates[0]


def map_shared_to_retailer_address(
    provider: GroceryProvider,
    shared_address: dict[str, Any],
    confirmations: ConfirmationStore,
) -> dict[str, Any]:
    """Map a shared address to a retailer delivery address.
    
    Workflow:
    1. List retailer's delivery addresses
    2. Try to find a match by postal code (and street if present)
    3. If no match found and provider supports HTTP address creation,
       prepare a confirmation for address creation
    4. If provider only supports browser-based address creation,
       return a guidance message
    
    Returns:
        - matched_address: The retailer address if found
        - needs_creation: True if address creation is needed
        - can_create_http: True if HTTP creation is available
        - confirmation_id: For address creation (if supported and needed)
        - guidance: Instructions if browser-based creation is required
    """
    
    # Get retailer addresses
    try:
        retailer_addresses = provider.delivery_addresses()
    except Exception as exc:
        raise ProviderError(f"failed to list delivery addresses: {exc}") from exc
    
    # Try to find a match
    postal_code = shared_address.get("postal_code", "")
    street = shared_address.get("street")
    
    matched = find_matching_address(retailer_addresses, postal_code, street)
    
    if matched:
        return {
            "matched": True,
            "matched_address": matched,
            "needs_creation": False,
        }
    
    # No match found - check if HTTP creation is available
    store_name = provider.name()
    
    # Currently, Mercadona and Gadis support HTTP address operations
    # Froiz and Eroski do not have verified HTTP contracts for address creation
    supports_http_creation = store_name in {"mercadona", "gadis"}
    
    if not supports_http_creation:
        return {
            "matched": False,
            "needs_creation": True,
            "can_create_http": False,
            "store": store_name,
            "guidance": (
                f"{store_name} does not have a verified HTTP contract for address creation. "
                "Use login_with_browser to open a session and manually add the delivery address, "
                "then call list_delivery_addresses again."
            ),
        }
    
    # HTTP creation is supported - prepare a confirmation
    # Note: This is a placeholder for prepare/commit pattern
    # Actual implementation would create a confirmation and return it
    
    return {
        "matched": False,
        "needs_creation": True,
        "can_create_http": True,
        "store": store_name,
        "shared_address": shared_address,
        "note": (
            "Address creation via HTTP is available for this store. "
            "However, the prepare/commit pattern for address creation "
            "is not yet implemented in this slice. "
            "Use login_with_browser and add the address manually for now."
        ),
    }


__all__ = ["find_matching_address", "map_shared_to_retailer_address"]
