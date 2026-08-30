"""Tests for shopping profile module."""

from __future__ import annotations

import pytest

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.shopping_profile import ShoppingProfile


def test_default_profile():
    profile = ShoppingProfile()
    data = profile.get_profile()
    
    assert data["default_max_total"] is None
    assert data["excluded_terms"] == []
    assert data["allergies"] == []
    assert data["private_label_preference"] == "any"
    assert data["include_loyalty_default"] is False
    assert data["substitution_policy"] == "allow"


def test_update_profile():
    profile = ShoppingProfile()
    
    result = profile.update_profile(
        default_max_total=50.0,
        excluded_terms=["gluten", "lactosa"],
        allergies=["frutos secos"],
        private_label_preference="prefer",
        include_loyalty_default=True,
        substitution_policy="prefer_brand",
        preferred_brands=["Hacendado", "Eroski"],
    )
    
    assert result["default_max_total"] == 50.0
    assert "gluten" in result["excluded_terms"]
    assert "frutos secos" in result["allergies"]
    assert result["private_label_preference"] == "prefer"
    assert result["include_loyalty_default"] is True
    assert result["substitution_policy"] == "prefer_brand"
    assert "Hacendado" in result["preferred_brands"]


def test_reset_profile():
    profile = ShoppingProfile()
    
    profile.update_profile(default_max_total=100.0, allergies=["test"])
    result = profile.reset_profile()
    
    assert result["default_max_total"] is None
    assert result["allergies"] == []


def test_invalid_preferences():
    profile = ShoppingProfile()
    
    with pytest.raises(InvalidRequest):
        profile.update_profile(default_max_total=-10.0)
    
    with pytest.raises(InvalidRequest):
        profile.update_profile(private_label_preference="invalid")
    
    with pytest.raises(InvalidRequest):
        profile.update_profile(substitution_policy="invalid")
