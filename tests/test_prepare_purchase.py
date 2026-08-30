"""Tests for prepare_purchase workflow."""

from __future__ import annotations

from open_grocery_mcp.drafts import DraftCartStore
from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.prepare_purchase import prepare_purchase
from open_grocery_mcp.registry import default_registry
import pytest


def test_prepare_purchase_with_items():
    registry = default_registry()
    drafts = DraftCartStore()
    
    items = ["leche", "pan"]
    
    result = prepare_purchase(
        registry,
        drafts,
        items=items,
        store="mercadona",
        postal_code="28001",
    )
    
    assert result["strategy"] == "single_store"
    assert result["store"] == "mercadona"
    assert "draft_id" in result
    assert "basket_result" in result


def test_prepare_purchase_comparison():
    registry = default_registry()
    drafts = DraftCartStore()
    
    items = [{"query": "leche", "quantity": 2}]
    
    result = prepare_purchase(
        registry,
        drafts,
        items=items,
        postal_code="28001",
    )
    
    assert result["strategy"] == "comparison"
    assert "recommended_store" in result
    assert "draft_id" in result


def test_prepare_purchase_with_profile():
    registry = default_registry()
    drafts = DraftCartStore()
    
    profile = {
        "default_max_total": 30.0,
        "include_loyalty_default": True,
        "excluded_terms": ["gluten"],
        "allergies": [],
        "private_label_preference": "prefer",
    }
    
    result = prepare_purchase(
        registry,
        drafts,
        items=["leche"],
        store="mercadona",
        postal_code="28001",
        profile=profile,
    )
    
    assert "profile_applied" in result
    assert result["profile_applied"]["include_loyalty"] is True


def test_prepare_purchase_no_items():
    registry = default_registry()
    drafts = DraftCartStore()
    
    with pytest.raises(InvalidRequest):
        prepare_purchase(registry, drafts)


def test_prepare_purchase_with_shopping_list():
    registry = default_registry()
    drafts = DraftCartStore()
    
    shopping_list_items = [
        {"item": "leche", "quantity": 1.0, "notes": None},
        {"item": "pan", "quantity": 2.0, "notes": "integral"},
    ]
    
    result = prepare_purchase(
        registry,
        drafts,
        shopping_list_items=shopping_list_items,
        store="mercadona",
        postal_code="28001",
    )
    
    assert result["strategy"] == "single_store"
    assert "draft_id" in result
