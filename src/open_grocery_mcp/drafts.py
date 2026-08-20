"""Local, non-purchasing cart drafts."""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from open_grocery_mcp.errors import InvalidRequest


class DraftCartStore:
    """Thread-safe in-memory drafts for one MCP process.

    A draft contains retailer product IDs and totals but never credentials,
    payment data or an order confirmation token.
    """

    def __init__(self, *, ttl_hours: int = 24) -> None:
        self._ttl = timedelta(hours=ttl_hours)
        self._drafts: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, basket_result: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        draft_id = uuid4().hex
        draft = {
            "draft_id": draft_id,
            "status": "local_draft",
            "created_at": now.isoformat(),
            "expires_at": (now + self._ttl).isoformat(),
            "retailer_cart_modified": False,
            "order_placed": False,
            "human_confirmation_required": True,
            "basket": deepcopy(basket_result),
        }
        with self._lock:
            self._purge_locked(now)
            self._drafts[draft_id] = draft
        return deepcopy(draft)

    def get(self, draft_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._lock:
            self._purge_locked(now)
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise InvalidRequest(f"unknown or expired draft_id {draft_id!r}")
            return deepcopy(draft)

    def delete(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            existed = self._drafts.pop(draft_id, None) is not None
        return {"draft_id": draft_id, "deleted": existed}

    def _purge_locked(self, now: datetime) -> None:
        expired = [
            key
            for key, value in self._drafts.items()
            if datetime.fromisoformat(value["expires_at"]) <= now
        ]
        for key in expired:
            self._drafts.pop(key, None)
