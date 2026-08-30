"""Local shared postal address book for default postal codes."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from open_grocery_mcp.errors import InvalidRequest


def _state_dir() -> Path:
    """Return the local state directory, creating it if necessary."""
    home = Path.home()
    state = home / ".open-grocery-mcp"
    state.mkdir(parents=True, exist_ok=True)
    return state


class SharedAddressBook:
    """Thread-safe local address book for default postal codes.
    
    Stores addresses in ~/.open-grocery-mcp/shared_addresses.json.
    One address can be marked as default.
    """

    def __init__(self) -> None:
        self._path = _state_dir() / "shared_addresses.json"
        self._lock = threading.Lock()

    def _load_locked(self) -> dict[str, Any]:
        """Load addresses with lock already held."""
        if not self._path.exists():
            return {"addresses": [], "default_address_id": None}
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {"addresses": [], "default_address_id": None}

    def _save_locked(self, data: dict[str, Any]) -> None:
        """Save addresses with lock already held."""
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_address(
        self,
        postal_code: str,
        label: str | None = None,
        street: str | None = None,
        city: str | None = None,
        set_as_default: bool = False,
    ) -> dict[str, Any]:
        """Add a new postal address to the shared book."""
        if not postal_code or len(postal_code.strip()) < 4:
            raise InvalidRequest("postal_code must be at least 4 characters")
        
        postal_code = postal_code.strip()
        
        with self._lock:
            data = self._load_locked()
            
            # Generate unique ID
            existing_ids = {addr["id"] for addr in data["addresses"]}
            address_id = f"addr_{len(data['addresses']) + 1}"
            counter = 2
            while address_id in existing_ids:
                address_id = f"addr_{len(data['addresses']) + counter}"
                counter += 1
            
            address = {
                "id": address_id,
                "postal_code": postal_code,
                "label": label,
                "street": street,
                "city": city,
            }
            
            data["addresses"].append(address)
            
            if set_as_default or len(data["addresses"]) == 1:
                data["default_address_id"] = address_id
            
            self._save_locked(data)
            
            return {
                "address": address,
                "is_default": data["default_address_id"] == address_id,
            }

    def list_addresses(self) -> dict[str, Any]:
        """List all shared addresses."""
        with self._lock:
            data = self._load_locked()
        
        return {
            "addresses": data["addresses"],
            "default_address_id": data["default_address_id"],
        }

    def get_default_address(self) -> dict[str, Any] | None:
        """Return the default address, or None if no default is set."""
        with self._lock:
            data = self._load_locked()
        
        if not data["default_address_id"]:
            return None
        
        for addr in data["addresses"]:
            if addr["id"] == data["default_address_id"]:
                return addr
        
        return None

    def set_default_address(self, address_id: str) -> dict[str, Any]:
        """Set an address as the default."""
        with self._lock:
            data = self._load_locked()
            
            found = False
            for addr in data["addresses"]:
                if addr["id"] == address_id:
                    found = True
                    break
            
            if not found:
                raise InvalidRequest(f"unknown address_id {address_id!r}")
            
            data["default_address_id"] = address_id
            self._save_locked(data)
        
        return {"default_address_id": address_id}

    def remove_address(self, address_id: str) -> dict[str, Any]:
        """Remove an address from the shared book."""
        with self._lock:
            data = self._load_locked()
            
            initial_count = len(data["addresses"])
            data["addresses"] = [
                addr for addr in data["addresses"]
                if addr["id"] != address_id
            ]
            
            removed = len(data["addresses"]) < initial_count
            
            if removed and data["default_address_id"] == address_id:
                data["default_address_id"] = (
                    data["addresses"][0]["id"] if data["addresses"] else None
                )
            
            self._save_locked(data)
        
        return {"address_id": address_id, "removed": removed}


__all__ = ["SharedAddressBook"]
