"""Local shopping profile with budget, restrictions and preferences."""

from __future__ import annotations

import json
import threading
from typing import Any

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.state_dir import get_state_dir, ensure_state_dir


class ShoppingProfile:
    """Thread-safe local shopping profile.
    
    Stores preferences in ~/.open-grocery-mcp/shopping_profile.json.
    Includes budget, allergies, private label preference, and defaults.
    """

    def __init__(self) -> None:
        self._path = get_state_dir() / "shopping_profile.json"
        self._lock = threading.Lock()

    def _load_locked(self) -> dict[str, Any]:
        """Load profile with lock already held."""
        if not self._path.exists():
            return {
                "default_max_total": None,
                "excluded_terms": [],
                "allergies": [],
                "private_label_preference": "any",
                "include_loyalty_default": False,
                "substitution_policy": "allow",
                "preferred_brands": [],
            }
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {
                "default_max_total": None,
                "excluded_terms": [],
                "allergies": [],
                "private_label_preference": "any",
                "include_loyalty_default": False,
                "substitution_policy": "allow",
                "preferred_brands": [],
            }

    def _save_locked(self, data: dict[str, Any]) -> None:
        """Save profile with lock already held."""
        # Ensure state directory exists before writing
        state_dir = ensure_state_dir()
        if state_dir is None:
            raise InvalidRequest("Cannot save shopping profile: state directory is not writable")
        
        # Update path if state_dir changed (e.g., fallback to /tmp)
        expected_path = state_dir / "shopping_profile.json"
        if self._path != expected_path:
            self._path = expected_path
        
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_profile(self) -> dict[str, Any]:
        """Get the current shopping profile."""
        with self._lock:
            return self._load_locked()

    def update_profile(
        self,
        default_max_total: float | None = None,
        excluded_terms: list[str] | None = None,
        allergies: list[str] | None = None,
        private_label_preference: str | None = None,
        include_loyalty_default: bool | None = None,
        substitution_policy: str | None = None,
        preferred_brands: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update the shopping profile.
        
        private_label_preference: "any", "prefer", "only", "never"
        substitution_policy: "allow", "prefer_brand", "never"
        """
        with self._lock:
            data = self._load_locked()
            
            if default_max_total is not None:
                if default_max_total < 0:
                    raise InvalidRequest("default_max_total cannot be negative")
                data["default_max_total"] = default_max_total
            
            if excluded_terms is not None:
                data["excluded_terms"] = [term.strip() for term in excluded_terms if term.strip()]
            
            if allergies is not None:
                data["allergies"] = [allergy.strip() for allergy in allergies if allergy.strip()]
            
            if private_label_preference is not None:
                valid_prefs = {"any", "prefer", "only", "never"}
                if private_label_preference not in valid_prefs:
                    raise InvalidRequest(
                        f"private_label_preference must be one of {valid_prefs}"
                    )
                data["private_label_preference"] = private_label_preference
            
            if include_loyalty_default is not None:
                data["include_loyalty_default"] = include_loyalty_default
            
            if substitution_policy is not None:
                valid_policies = {"allow", "prefer_brand", "never"}
                if substitution_policy not in valid_policies:
                    raise InvalidRequest(
                        f"substitution_policy must be one of {valid_policies}"
                    )
                data["substitution_policy"] = substitution_policy
            
            if preferred_brands is not None:
                data["preferred_brands"] = [
                    brand.strip() for brand in preferred_brands if brand.strip()
                ]
            
            self._save_locked(data)
            
            return data

    def reset_profile(self) -> dict[str, Any]:
        """Reset the profile to default values."""
        with self._lock:
            data = {
                "default_max_total": None,
                "excluded_terms": [],
                "allergies": [],
                "private_label_preference": "any",
                "include_loyalty_default": False,
                "substitution_policy": "allow",
                "preferred_brands": [],
            }
            self._save_locked(data)
            return data


__all__ = ["ShoppingProfile"]
