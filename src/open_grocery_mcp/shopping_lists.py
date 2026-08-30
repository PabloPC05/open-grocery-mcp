"""Local shopping lists with recurring items and last basket replay."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.state_dir import get_state_dir


class ShoppingListStore:
    """Thread-safe local shopping list storage.
    
    Stores lists in ~/.open-grocery-mcp/shopping_lists.json.
    Supports named lists with items (quantity + optional notes).
    Stores the last completed basket for replay.
    """

    def __init__(self) -> None:
        self._path = get_state_dir() / "shopping_lists.json"
        self._lock = threading.Lock()

    def _load_locked(self) -> dict[str, Any]:
        """Load lists with lock already held."""
        if not self._path.exists():
            return {
                "lists": [
                    {
                        "list_id": "habitual",
                        "name": "Habitual",
                        "items": [],
                        "created_at": datetime.now(UTC).isoformat(),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ],
                "last_basket": None,
            }
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {
                "lists": [
                    {
                        "list_id": "habitual",
                        "name": "Habitual",
                        "items": [],
                        "created_at": datetime.now(UTC).isoformat(),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ],
                "last_basket": None,
            }

    def _save_locked(self, data: dict[str, Any]) -> None:
        """Save lists with lock already held."""
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def create_list(self, name: str, list_id: str | None = None) -> dict[str, Any]:
        """Create a new shopping list."""
        if not name or not name.strip():
            raise InvalidRequest("name cannot be empty")
        
        with self._lock:
            data = self._load_locked()
            
            if list_id is None:
                base_id = name.lower().replace(" ", "_")
                list_id = base_id
                counter = 2
                existing_ids = {lst["list_id"] for lst in data["lists"]}
                while list_id in existing_ids:
                    list_id = f"{base_id}_{counter}"
                    counter += 1
            else:
                existing_ids = {lst["list_id"] for lst in data["lists"]}
                if list_id in existing_ids:
                    raise InvalidRequest(f"list_id {list_id!r} already exists")
            
            now = datetime.now(UTC).isoformat()
            shopping_list = {
                "list_id": list_id,
                "name": name,
                "items": [],
                "created_at": now,
                "updated_at": now,
            }
            
            data["lists"].append(shopping_list)
            self._save_locked(data)
            
            return shopping_list

    def list_lists(self) -> list[dict[str, Any]]:
        """List all shopping lists."""
        with self._lock:
            data = self._load_locked()
        
        return [
            {
                "list_id": lst["list_id"],
                "name": lst["name"],
                "item_count": len(lst["items"]),
                "created_at": lst["created_at"],
                "updated_at": lst["updated_at"],
            }
            for lst in data["lists"]
        ]

    def get_list(self, list_id: str) -> dict[str, Any]:
        """Get a shopping list by ID."""
        with self._lock:
            data = self._load_locked()
        
        for lst in data["lists"]:
            if lst["list_id"] == list_id:
                return lst
        
        raise InvalidRequest(f"unknown list_id {list_id!r}")

    def add_item(
        self,
        list_id: str,
        item: str,
        quantity: float = 1.0,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Add an item to a shopping list."""
        if not item or not item.strip():
            raise InvalidRequest("item cannot be empty")
        if quantity <= 0:
            raise InvalidRequest("quantity must be positive")
        
        with self._lock:
            data = self._load_locked()
            
            found_list = None
            for lst in data["lists"]:
                if lst["list_id"] == list_id:
                    found_list = lst
                    break
            
            if found_list is None:
                raise InvalidRequest(f"unknown list_id {list_id!r}")
            
            item_entry = {
                "item": item.strip(),
                "quantity": quantity,
                "notes": notes,
            }
            
            found_list["items"].append(item_entry)
            found_list["updated_at"] = datetime.now(UTC).isoformat()
            
            self._save_locked(data)
            
            return {"list_id": list_id, "item": item_entry}

    def update_item(
        self,
        list_id: str,
        item_index: int,
        item: str | None = None,
        quantity: float | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Update an item in a shopping list."""
        with self._lock:
            data = self._load_locked()
            
            found_list = None
            for lst in data["lists"]:
                if lst["list_id"] == list_id:
                    found_list = lst
                    break
            
            if found_list is None:
                raise InvalidRequest(f"unknown list_id {list_id!r}")
            
            if item_index < 0 or item_index >= len(found_list["items"]):
                raise InvalidRequest(f"item_index {item_index} out of range")
            
            target = found_list["items"][item_index]
            
            if item is not None:
                if not item.strip():
                    raise InvalidRequest("item cannot be empty")
                target["item"] = item.strip()
            
            if quantity is not None:
                if quantity <= 0:
                    raise InvalidRequest("quantity must be positive")
                target["quantity"] = quantity
            
            if notes is not None:
                target["notes"] = notes
            
            found_list["updated_at"] = datetime.now(UTC).isoformat()
            
            self._save_locked(data)
            
            return {"list_id": list_id, "item_index": item_index, "item": target}

    def remove_item(self, list_id: str, item_index: int) -> dict[str, Any]:
        """Remove an item from a shopping list."""
        with self._lock:
            data = self._load_locked()
            
            found_list = None
            for lst in data["lists"]:
                if lst["list_id"] == list_id:
                    found_list = lst
                    break
            
            if found_list is None:
                raise InvalidRequest(f"unknown list_id {list_id!r}")
            
            if item_index < 0 or item_index >= len(found_list["items"]):
                raise InvalidRequest(f"item_index {item_index} out of range")
            
            removed_item = found_list["items"].pop(item_index)
            found_list["updated_at"] = datetime.now(UTC).isoformat()
            
            self._save_locked(data)
            
            return {"list_id": list_id, "item_index": item_index, "removed": removed_item}

    def delete_list(self, list_id: str) -> dict[str, Any]:
        """Delete a shopping list."""
        with self._lock:
            data = self._load_locked()
            
            initial_count = len(data["lists"])
            data["lists"] = [lst for lst in data["lists"] if lst["list_id"] != list_id]
            
            removed = len(data["lists"]) < initial_count
            
            self._save_locked(data)
        
        return {"list_id": list_id, "deleted": removed}

    def store_last_basket(self, basket_result: dict[str, Any]) -> dict[str, Any]:
        """Store the last prepared/committed basket for replay."""
        with self._lock:
            data = self._load_locked()
            
            data["last_basket"] = {
                "stored_at": datetime.now(UTC).isoformat(),
                "basket": basket_result,
            }
            
            self._save_locked(data)
        
        return {"status": "stored", "stored_at": data["last_basket"]["stored_at"]}

    def get_last_basket(self) -> dict[str, Any] | None:
        """Retrieve the last stored basket for replay."""
        with self._lock:
            data = self._load_locked()
        
        return data.get("last_basket")


__all__ = ["ShoppingListStore"]
