"""Tests for shared addresses module."""

from __future__ import annotations

import pytest

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.shared_addresses import SharedAddressBook


def test_add_and_list_addresses():
    book = SharedAddressBook()
    
    result = book.add_address(
        postal_code="28001",
        label="Casa",
        street="Calle Mayor 1",
        city="Madrid",
        set_as_default=True,
    )
    
    assert result["address"]["postal_code"] == "28001"
    assert result["address"]["label"] == "Casa"
    assert result["is_default"] is True
    
    addresses = book.list_addresses()
    assert len(addresses["addresses"]) >= 1
    assert addresses["default_address_id"] == result["address"]["id"]


def test_get_default_address():
    book = SharedAddressBook()
    
    book.add_address("15001", label="Trabajo", set_as_default=True)
    
    default = book.get_default_address()
    assert default is not None
    assert default["postal_code"] == "15001"
    assert default["label"] == "Trabajo"


def test_set_default_address():
    book = SharedAddressBook()
    
    first = book.add_address("28001", label="Primera")
    second = book.add_address("15001", label="Segunda")
    
    book.set_default_address(second["address"]["id"])
    
    default = book.get_default_address()
    assert default["id"] == second["address"]["id"]


def test_remove_address():
    book = SharedAddressBook()
    
    result = book.add_address("28001", label="Temporal")
    address_id = result["address"]["id"]
    
    removed = book.remove_address(address_id)
    assert removed["removed"] is True
    
    addresses = book.list_addresses()
    assert not any(addr["id"] == address_id for addr in addresses["addresses"])


def test_invalid_postal_code():
    book = SharedAddressBook()
    
    with pytest.raises(InvalidRequest):
        book.add_address("")
    
    with pytest.raises(InvalidRequest):
        book.add_address("123")


def test_invalid_address_id():
    book = SharedAddressBook()
    
    with pytest.raises(InvalidRequest):
        book.set_default_address("nonexistent")
