"""Tests for delivery intent resolution."""

from __future__ import annotations

from datetime import date, timedelta

from open_grocery_mcp.delivery_intent import resolve_delivery_slot
from open_grocery_mcp.errors import InvalidRequest
import pytest


def test_next_available():
    slots = [
        {"id": "1", "date": "2026-08-31", "start": "10:00", "available": True},
        {"id": "2", "date": "2026-09-01", "start": "12:00", "available": True},
    ]
    
    result = resolve_delivery_slot(slots, "next_available")
    assert result["matched"] is True
    assert result["slot"]["id"] == "1"


def test_today():
    today = date.today().isoformat()
    slots = [
        {"id": "1", "date": today, "start": "14:00", "available": True},
        {"id": "2", "date": today, "start": "16:00", "available": True},
    ]
    
    result = resolve_delivery_slot(slots, "today")
    assert result["matched"] is True
    assert result["slot"]["id"] == "1"


def test_tomorrow():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    slots = [
        {"id": "1", "date": tomorrow, "start": "10:00", "available": True},
    ]
    
    result = resolve_delivery_slot(slots, "mañana")
    assert result["matched"] is True
    assert result["slot"]["id"] == "1"


def test_morning_time():
    slots = [
        {"id": "1", "date": "2026-09-01", "start": "09:00", "available": True},
        {"id": "2", "date": "2026-09-01", "start": "15:00", "available": True},
    ]
    
    result = resolve_delivery_slot(slots, "morning")
    assert result["matched"] is True
    assert result["slot"]["id"] == "1"


def test_afternoon_time():
    slots = [
        {"id": "1", "date": "2026-09-01", "start": "09:00", "available": True},
        {"id": "2", "date": "2026-09-01", "start": "15:00", "available": True},
    ]
    
    result = resolve_delivery_slot(slots, "tarde")
    assert result["matched"] is True
    assert result["slot"]["id"] == "2"


def test_specific_date_iso():
    slots = [
        {"id": "1", "date": "2026-09-05", "start": "10:00", "available": True},
        {"id": "2", "date": "2026-09-05", "start": "14:00", "available": True},
    ]
    
    result = resolve_delivery_slot(slots, "2026-09-05")
    assert result["matched"] is True
    assert result["slot"]["date"] == "2026-09-05"


def test_no_match_returns_nearest():
    slots = [
        {"id": "1", "date": "2026-09-10", "start": "10:00", "available": True},
    ]
    
    result = resolve_delivery_slot(slots, "today")
    assert result["matched"] is False
    assert "nearest_options" in result


def test_no_slots_available():
    with pytest.raises(InvalidRequest):
        resolve_delivery_slot([], "next_available")


def test_all_unavailable():
    slots = [
        {"id": "1", "date": "2026-09-01", "start": "10:00", "available": False},
    ]
    
    result = resolve_delivery_slot(slots, "next_available")
    assert result["matched"] is False
    assert result["reason"] == "no_available_slots"
