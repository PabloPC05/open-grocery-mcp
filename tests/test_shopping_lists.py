"""Tests for shopping lists module."""

from __future__ import annotations

import pytest

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.shopping_lists import ShoppingListStore


def test_default_habitual_list_exists():
    store = ShoppingListStore()
    lists = store.list_lists()
    assert len(lists) >= 1
    assert any(lst["list_id"] == "habitual" for lst in lists)


def test_create_and_get_list():
    store = ShoppingListStore()
    created = store.create_list("Test List")
    
    assert "list_id" in created
    assert created["name"] == "Test List"
    assert created["items"] == []
    
    retrieved = store.get_list(created["list_id"])
    assert retrieved["name"] == "Test List"


def test_add_and_remove_items():
    store = ShoppingListStore()
    created = store.create_list("Test List")
    list_id = created["list_id"]
    
    result = store.add_item(list_id, "leche", quantity=2.0, notes="desnatada")
    assert result["item"]["item"] == "leche"
    assert result["item"]["quantity"] == 2.0
    assert result["item"]["notes"] == "desnatada"
    
    shopping_list = store.get_list(list_id)
    assert len(shopping_list["items"]) == 1
    
    store.remove_item(list_id, 0)
    shopping_list = store.get_list(list_id)
    assert len(shopping_list["items"]) == 0


def test_update_item():
    store = ShoppingListStore()
    created = store.create_list("Test List")
    list_id = created["list_id"]
    
    store.add_item(list_id, "pan", quantity=1.0)
    result = store.update_item(list_id, 0, quantity=3.0, notes="integral")
    
    assert result["item"]["quantity"] == 3.0
    assert result["item"]["notes"] == "integral"


def test_last_basket_storage():
    store = ShoppingListStore()
    
    basket = {
        "store": "mercadona",
        "total": 25.50,
        "items": [{"name": "leche", "price": 1.50}],
    }
    
    result = store.store_last_basket(basket)
    assert result["status"] == "stored"
    
    retrieved = store.get_last_basket()
    assert retrieved is not None
    assert retrieved["basket"]["store"] == "mercadona"


def test_invalid_operations():
    store = ShoppingListStore()
    
    with pytest.raises(InvalidRequest):
        store.create_list("")
    
    with pytest.raises(InvalidRequest):
        store.get_list("nonexistent")
    
    with pytest.raises(InvalidRequest):
        store.add_item("habitual", "", quantity=1.0)
    
    with pytest.raises(InvalidRequest):
        store.add_item("habitual", "item", quantity=-1.0)
