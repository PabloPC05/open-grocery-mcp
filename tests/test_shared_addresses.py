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
    # Ensure the temp directory exists
    tmp_path.mkdir(parents=True, exist_ok=True)
    
    # Patch the functions where they are used, not where they are defined
    with patch("open_grocery_mcp.shared_addresses.get_state_dir", return_value=tmp_path), \
         patch("open_grocery_mcp.shared_addresses.ensure_state_dir", return_value=tmp_path):
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


# New tests for postal code resolution


def test_validate_spanish_postal_code():
    """Test Spanish postal code validation."""
    # Valid codes
    assert shared_addresses.validate_spanish_postal_code("15001") is True
    assert shared_addresses.validate_spanish_postal_code("28001") is True
    assert shared_addresses.validate_spanish_postal_code("00000") is True
    assert shared_addresses.validate_spanish_postal_code("99999") is True
    assert shared_addresses.validate_spanish_postal_code("  15001  ") is True
    
    # Invalid codes
    assert shared_addresses.validate_spanish_postal_code("") is False
    assert shared_addresses.validate_spanish_postal_code("1500") is False
    assert shared_addresses.validate_spanish_postal_code("150011") is False
    assert shared_addresses.validate_spanish_postal_code("15O01") is False
    assert shared_addresses.validate_spanish_postal_code("abcde") is False
    assert shared_addresses.validate_spanish_postal_code(None) is False


def test_resolve_postal_code_explicit_wins(temp_state_dir):
    """Explicit postal_code argument takes priority."""
    # Set a default
    shared_addresses.add_postal_address(
        postal_code="15001",
        label="Default",
    )
    
    # Explicit argument should win
    resolved, source = shared_addresses.resolve_postal_code("28001")
    assert resolved == "28001"
    assert source == "argument"


def test_resolve_postal_code_from_shared_default(temp_state_dir):
    """Resolve from shared default address when explicit is None."""
    shared_addresses.add_postal_address(
        postal_code="15001",
        label="Home",
    )
    
    resolved, source = shared_addresses.resolve_postal_code(None)
    assert resolved == "15001"
    assert source == "shared_default"


def test_resolve_postal_code_from_env(temp_state_dir, monkeypatch):
    """Resolve from environment variable when no shared default."""
    monkeypatch.setenv("OPEN_GROCERY_DEFAULT_POSTAL_CODE", "28001")
    
    resolved, source = shared_addresses.resolve_postal_code(None)
    assert resolved == "28001"
    assert source == "env"


def test_resolve_postal_code_priority_order(temp_state_dir, monkeypatch):
    """Test full priority order: argument > shared > env > none."""
    # Set both shared and env
    shared_addresses.add_postal_address(
        postal_code="15001",
        label="Shared",
    )
    monkeypatch.setenv("OPEN_GROCERY_DEFAULT_POSTAL_CODE", "08001")
    
    # Explicit wins
    resolved, source = shared_addresses.resolve_postal_code("28001")
    assert resolved == "28001"
    assert source == "argument"
    
    # Shared wins over env
    resolved, source = shared_addresses.resolve_postal_code(None)
    assert resolved == "15001"
    assert source == "shared_default"


def test_resolve_postal_code_none_when_no_default(temp_state_dir):
    """Return None when no default is available."""
    resolved, source = shared_addresses.resolve_postal_code(None)
    assert resolved is None
    assert source == "none"


def test_resolve_postal_code_strips_whitespace(temp_state_dir):
    """Postal code is stripped of whitespace."""
    resolved, source = shared_addresses.resolve_postal_code("  28001  ")
    assert resolved == "28001"
    assert source == "argument"


def test_set_default_postal_code_creates_address(temp_state_dir):
    """set_default_postal_code creates and sets default address."""
    result = shared_addresses.set_default_postal_code(
        postal_code="15001",
        city="A Coruña",
    )
    
    assert result["address"]["postal_code"] == "15001"
    assert result["address"]["city"] == "A Coruña"
    assert result["address"]["label"] == "Default"
    assert result["is_default"] is True
    
    # Verify it's actually set as default
    default = shared_addresses.get_default_address()
    assert default is not None
    assert default["postal_code"] == "15001"


def test_set_default_postal_code_validates_format(temp_state_dir):
    """set_default_postal_code validates Spanish postal code format."""
    with pytest.raises(InvalidRequest, match="Invalid Spanish postal code format"):
        shared_addresses.set_default_postal_code("1500")
    
    with pytest.raises(InvalidRequest, match="Invalid Spanish postal code format"):
        shared_addresses.set_default_postal_code("abcde")
    
    with pytest.raises(InvalidRequest, match="Invalid Spanish postal code format"):
        shared_addresses.set_default_postal_code("150011")


def test_set_default_postal_code_requires_postal_code(temp_state_dir):
    """set_default_postal_code requires a non-empty postal code."""
    with pytest.raises(InvalidRequest, match="postal_code is required"):
        shared_addresses.set_default_postal_code("")
    
    with pytest.raises(InvalidRequest, match="postal_code is required"):
        shared_addresses.set_default_postal_code("   ")


def test_set_default_postal_code_updates_existing(temp_state_dir):
    """set_default_postal_code can update the default."""
    # Set first default
    shared_addresses.set_default_postal_code("15001", city="A Coruña")
    
    # Set new default
    result = shared_addresses.set_default_postal_code("28001", city="Madrid", label="Madrid Home")
    
    assert result["address"]["postal_code"] == "28001"
    assert result["address"]["city"] == "Madrid"
    assert result["address"]["label"] == "Madrid Home"
    
    # New one should be default
    default = shared_addresses.get_default_address()
    assert default["postal_code"] == "28001"


def test_set_default_postal_code_with_custom_label(temp_state_dir):
    """set_default_postal_code accepts custom label."""
    result = shared_addresses.set_default_postal_code(
        postal_code="15001",
        label="My Custom Label",
    )
    
    assert result["address"]["label"] == "My Custom Label"


def test_set_default_postal_code_upserts_not_duplicates(temp_state_dir):
    """Calling set_default_postal_code twice updates in place, does not duplicate."""
    # First call creates address
    result1 = shared_addresses.set_default_postal_code(
        postal_code="15001",
        city="A Coruña",
        label="First",
    )
    first_id = result1["address"]["id"]
    
    # Verify one address exists
    addresses = shared_addresses.list_shared_addresses()
    assert addresses["count"] == 1
    assert addresses["addresses"][0]["postal_code"] == "15001"
    assert addresses["addresses"][0]["city"] == "A Coruña"
    assert addresses["addresses"][0]["label"] == "First"
    
    # Second call should update the existing default, not create a new one
    result2 = shared_addresses.set_default_postal_code(
        postal_code="28001",
        city="Madrid",
        label="Second",
    )
    second_id = result2["address"]["id"]
    
    # Should still have exactly one address (not two)
    addresses = shared_addresses.list_shared_addresses()
    assert addresses["count"] == 1, "Should have exactly 1 address, not duplicate"
    
    # The address should have been updated in place (same ID)
    assert second_id == first_id, "Should update existing address, not create new one"
    
    # The address should have the new values
    assert addresses["addresses"][0]["postal_code"] == "28001"
    assert addresses["addresses"][0]["city"] == "Madrid"
    assert addresses["addresses"][0]["label"] == "Second"
    assert addresses["addresses"][0]["is_default"] is True
    
    # Default should still point to the same (updated) address
    default = shared_addresses.get_default_address()
    assert default["id"] == first_id
    assert default["postal_code"] == "28001"


def test_set_default_postal_code_third_call_still_upserts(temp_state_dir):
    """Even a third call continues to upsert, never duplicating."""
    shared_addresses.set_default_postal_code("15001", city="A Coruña")
    shared_addresses.set_default_postal_code("28001", city="Madrid")
    shared_addresses.set_default_postal_code("08001", city="Barcelona")
    
    # Should still have exactly one address
    addresses = shared_addresses.list_shared_addresses()
    assert addresses["count"] == 1
    assert addresses["addresses"][0]["postal_code"] == "08001"
    assert addresses["addresses"][0]["city"] == "Barcelona"
