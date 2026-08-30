"""Resolve delivery slots by intent rather than raw slot_id."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from open_grocery_mcp.errors import InvalidRequest


def resolve_delivery_slot(
    slots: list[dict[str, Any]],
    intent: str,
) -> dict[str, Any]:
    """Resolve a delivery slot by natural intent.
    
    Supported intents:
    - "next_available", "próximo", "primero"
    - "today", "hoy"
    - "tomorrow", "mañana"
    - "monday", "lunes", "tuesday", "martes", etc.
    - "morning", "mañana" (time), "tarde", "afternoon", "evening", "noche"
    - Specific date: "2026-08-31", "31/08/2026"
    
    Returns the best matching slot or nearest options if no exact match.
    """
    if not slots:
        raise InvalidRequest("no delivery slots available")
    
    intent_lower = intent.lower().strip()
    
    available_slots = [slot for slot in slots if slot.get("available", False)]
    
    if not available_slots:
        return {
            "matched": False,
            "reason": "no_available_slots",
            "nearest_options": slots[:3],
        }
    
    today = date.today()
    
    # Next available
    if intent_lower in {"next_available", "próximo", "primero", "first", "asap"}:
        earliest = min(
            available_slots,
            key=lambda s: (s.get("date", ""), s.get("start", "")),
        )
        return {
            "matched": True,
            "intent": "next_available",
            "slot": earliest,
        }
    
    # Today
    if intent_lower in {"today", "hoy"}:
        today_str = today.isoformat()
        today_slots = [s for s in available_slots if s.get("date") == today_str]
        if today_slots:
            earliest = min(today_slots, key=lambda s: s.get("start", ""))
            return {
                "matched": True,
                "intent": "today",
                "slot": earliest,
            }
        return {
            "matched": False,
            "reason": "no_slots_today",
            "nearest_options": available_slots[:3],
        }
    
    # Tomorrow
    if intent_lower in {"tomorrow", "mañana"}:
        tomorrow = (today + timedelta(days=1)).isoformat()
        tomorrow_slots = [s for s in available_slots if s.get("date") == tomorrow]
        if tomorrow_slots:
            earliest = min(tomorrow_slots, key=lambda s: s.get("start", ""))
            return {
                "matched": True,
                "intent": "tomorrow",
                "slot": earliest,
            }
        return {
            "matched": False,
            "reason": "no_slots_tomorrow",
            "nearest_options": available_slots[:3],
        }
    
    # Day of week
    weekday_map = {
        "monday": 0, "lunes": 0,
        "tuesday": 1, "martes": 1,
        "wednesday": 2, "miércoles": 2, "miercoles": 2,
        "thursday": 3, "jueves": 3,
        "friday": 4, "viernes": 4,
        "saturday": 5, "sábado": 5, "sabado": 5,
        "sunday": 6, "domingo": 6,
    }
    
    if intent_lower in weekday_map:
        target_weekday = weekday_map[intent_lower]
        days_ahead = (target_weekday - today.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = (today + timedelta(days=days_ahead)).isoformat()
        
        day_slots = [s for s in available_slots if s.get("date") == target_date]
        if day_slots:
            earliest = min(day_slots, key=lambda s: s.get("start", ""))
            return {
                "matched": True,
                "intent": intent_lower,
                "slot": earliest,
            }
        return {
            "matched": False,
            "reason": f"no_slots_on_{intent_lower}",
            "nearest_options": available_slots[:3],
        }
    
    # Time of day
    if intent_lower in {"morning", "mañana", "morning_time"}:
        morning_slots = [
            s for s in available_slots
            if s.get("start", "")[:2] in {"07", "08", "09", "10", "11"}
        ]
        if morning_slots:
            earliest = min(
                morning_slots,
                key=lambda s: (s.get("date", ""), s.get("start", "")),
            )
            return {
                "matched": True,
                "intent": "morning",
                "slot": earliest,
            }
        return {
            "matched": False,
            "reason": "no_morning_slots",
            "nearest_options": available_slots[:3],
        }
    
    if intent_lower in {"afternoon", "tarde"}:
        afternoon_slots = [
            s for s in available_slots
            if s.get("start", "")[:2] in {"12", "13", "14", "15", "16", "17"}
        ]
        if afternoon_slots:
            earliest = min(
                afternoon_slots,
                key=lambda s: (s.get("date", ""), s.get("start", "")),
            )
            return {
                "matched": True,
                "intent": "afternoon",
                "slot": earliest,
            }
        return {
            "matched": False,
            "reason": "no_afternoon_slots",
            "nearest_options": available_slots[:3],
        }
    
    if intent_lower in {"evening", "noche", "night"}:
        evening_slots = [
            s for s in available_slots
            if s.get("start", "")[:2] in {"18", "19", "20", "21", "22"}
        ]
        if evening_slots:
            earliest = min(
                evening_slots,
                key=lambda s: (s.get("date", ""), s.get("start", "")),
            )
            return {
                "matched": True,
                "intent": "evening",
                "slot": earliest,
            }
        return {
            "matched": False,
            "reason": "no_evening_slots",
            "nearest_options": available_slots[:3],
        }
    
    # Specific date (ISO or DD/MM/YYYY)
    iso_match = re.match(r"^\d{4}-\d{2}-\d{2}$", intent_lower)
    dmy_match = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", intent_lower)
    
    if iso_match:
        target_date = intent_lower
        date_slots = [s for s in available_slots if s.get("date") == target_date]
        if date_slots:
            earliest = min(date_slots, key=lambda s: s.get("start", ""))
            return {
                "matched": True,
                "intent": f"date_{target_date}",
                "slot": earliest,
            }
        return {
            "matched": False,
            "reason": f"no_slots_on_{target_date}",
            "nearest_options": available_slots[:3],
        }
    
    if dmy_match:
        day, month, year = dmy_match.groups()
        try:
            parsed_date = date(int(year), int(month), int(day))
            target_date = parsed_date.isoformat()
            date_slots = [s for s in available_slots if s.get("date") == target_date]
            if date_slots:
                earliest = min(date_slots, key=lambda s: s.get("start", ""))
                return {
                    "matched": True,
                    "intent": f"date_{target_date}",
                    "slot": earliest,
                }
            return {
                "matched": False,
                "reason": f"no_slots_on_{target_date}",
                "nearest_options": available_slots[:3],
            }
        except ValueError:
            pass
    
    # Unrecognized intent
    return {
        "matched": False,
        "reason": "unrecognized_intent",
        "provided_intent": intent,
        "nearest_options": available_slots[:3],
    }


__all__ = ["resolve_delivery_slot"]
