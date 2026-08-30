"""Tests for shared postal addresses."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from open_grocery_mcp import shared_addresses
from open_grocery_mcp.errors import InvalidRequest


@pytest.fixture
def temp_state_dir(tmp_path):
    """Provide a temporary state directory for testing."""
    with patch.object(shared_addresses, "_state_dir", return_value=tmp_path):
        yield tmp_path


def test_add_first_address_becomes_default(temp_state_dir):
    """Adding the first address sets it as default."""
    result = shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
        street="Calle Mayor",
        number="1",
        city="Madrid",
    )
    
    assert result["address"]["postal_code"] == "28001"
    assert result["address"]["label"] == "Home"
    assert result["is_default"] is True
    
    # Verify it's saved
    addresses_file = temp_state_dir / "shared_addresses.json"
    assert addresses_file.exists()
    
    with addresses_file.open() as f:
        data = json.load(f)
    
    assert len(data["addresses"]) == 1
    assert data["default_id"] == result["address"]["id"]


def test_add_multiple_addresses(temp_state_dir):
    """Can add multiple addresses."""
    result1 = shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
    )
    
    result2 = shared_addresses.add_postal_address(
        postal_code="15001",
        label="Work",
        set_as_default=False,
    )
    
    assert result1["is_default"] is True
    assert result2["is_default"] is False
    
    addresses = shared_addresses.list_shared_addresses()
    assert addresses["count"] == 2
    assert addresses["default_id"] == result1["address"]["id"]


def test_postal_code_required():
    """postal_code is required."""
    with pytest.raises(InvalidRequest, match="postal_code is required"):
        shared_addresses.add_postal_address(postal_code="")
    
    with pytest.raises(InvalidRequest, match="postal_code is required"):
        shared_addresses.add_postal_address(postal_code="   ")


def test_list_empty_addresses(temp_state_dir):
    """Listing when no addresses returns empty."""
    result = shared_addresses.list_shared_addresses()
    assert result["count"] == 0
    assert result["addresses"] == []
    assert result["default_id"] is None


def test_list_marks_default(temp_state_dir):
    """list_shared_addresses marks the default address."""
    shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
    )
    
    shared_addresses.add_postal_address(
        postal_code="15001",
        label="Work",
        set_as_default=False,
    )
    
    result = shared_addresses.list_shared_addresses()
    
    home = next(a for a in result["addresses"] if a["label"] == "Home")
    work = next(a for a in result["addresses"] if a["label"] == "Work")
    
    assert home["is_default"] is True
    assert work["is_default"] is False


def test_get_default_address(temp_state_dir):
    """get_default_address returns the default."""
    shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
    )
    
    default = shared_addresses.get_default_address()
    assert default is not None
    assert default["postal_code"] == "28001"
    assert default["label"] == "Home"


def test_get_default_address_when_none(temp_state_dir):
    """get_default_address returns None when no addresses."""
    default = shared_addresses.get_default_address()
    assert default is None


def test_set_default_address(temp_state_dir):
    """Can change the default address."""
    shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
    )
    
    addr2 = shared_addresses.add_postal_address(
        postal_code="15001",
        label="Work",
        set_as_default=False,
    )
    
    # Change default to Work
    result = shared_addresses.set_default_address(addr2["address"]["id"])
    assert result["address_id"] == addr2["address"]["id"]
    
    default = shared_addresses.get_default_address()
    assert default["label"] == "Work"


def test_set_default_nonexistent_fails(temp_state_dir):
    """Setting nonexistent address as default fails."""
    with pytest.raises(InvalidRequest, match="not found"):
        shared_addresses.set_default_address(999)


def test_remove_address(temp_state_dir):
    """Can remove an address."""
    addr = shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
    )
    
    result = shared_addresses.remove_postal_address(addr["address"]["id"])
    assert result["address_id"] == addr["address"]["id"]
    
    addresses = shared_addresses.list_shared_addresses()
    assert addresses["count"] == 0


def test_remove_default_sets_new_default(temp_state_dir):
    """Removing default address sets another as default."""
    addr1 = shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
    )
    
    addr2 = shared_addresses.add_postal_address(
        postal_code="15001",
        label="Work",
        set_as_default=False,
    )
    
    # Remove the default (Home)
    result = shared_addresses.remove_postal_address(addr1["address"]["id"])
    assert result["new_default_id"] == addr2["address"]["id"]
    
    default = shared_addresses.get_default_address()
    assert default["label"] == "Work"


def test_remove_nonexistent_fails(temp_state_dir):
    """Removing nonexistent address fails."""
    with pytest.raises(InvalidRequest, match="not found"):
        shared_addresses.remove_postal_address(999)


def test_get_default_postal_code(temp_state_dir):
    """get_default_postal_code returns just the postal code."""
    shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
    )
    
    postal_code = shared_addresses.get_default_postal_code()
    assert postal_code == "28001"


def test_get_default_postal_code_when_none(temp_state_dir):
    """get_default_postal_code returns None when no default."""
    postal_code = shared_addresses.get_default_postal_code()
    assert postal_code is None


def test_addresses_file_permissions(temp_state_dir):
    """Addresses file is created with restricted permissions."""
    shared_addresses.add_postal_address(
        postal_code="28001",
        label="Home",
    )
    
    addresses_file = temp_state_dir / "shared_addresses.json"
    # Check file permissions are user-only (0o600)
    assert addresses_file.stat().st_mode & 0o777 == 0o600
