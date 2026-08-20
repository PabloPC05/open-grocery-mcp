"""Short-lived, one-use confirmations for state-changing retailer actions."""

from __future__ import annotations

import hmac
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from open_grocery_mcp.errors import ConfirmationRequired


@dataclass(slots=True)
class _PendingConfirmation:
    action: str
    phrase: str
    payload: dict[str, Any]
    summary: dict[str, Any]
    expires_at: datetime


class ConfirmationStore:
    """Thread-safe, in-memory, one-use confirmation tokens."""

    def __init__(self, *, ttl_seconds: int = 300) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._pending: dict[str, _PendingConfirmation] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        action: str,
        phrase: str,
        payload: dict[str, Any],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        confirmation_id = uuid4().hex
        entry = _PendingConfirmation(
            action=action,
            phrase=phrase,
            payload=deepcopy(payload),
            summary=deepcopy(summary),
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._purge_locked(now)
            self._pending[confirmation_id] = entry
        return {
            "confirmation_id": confirmation_id,
            "action": action,
            "confirmation_phrase": phrase,
            "expires_at": entry.expires_at.isoformat(),
            "summary": deepcopy(summary),
            "state_changed": False,
        }

    def consume(self, confirmation_id: str, phrase: str, *, action: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._lock:
            self._purge_locked(now)
            entry = self._pending.get(confirmation_id)
            if entry is None:
                raise ConfirmationRequired("unknown, expired, or already-used confirmation_id")
            if entry.action != action:
                raise ConfirmationRequired("confirmation_id belongs to a different action")
            if not hmac.compare_digest(entry.phrase, phrase):
                raise ConfirmationRequired("confirmation phrase does not match the reviewed action")
            self._pending.pop(confirmation_id, None)
            return deepcopy(entry.payload)

    def _purge_locked(self, now: datetime) -> None:
        for key, value in tuple(self._pending.items()):
            if value.expires_at <= now:
                self._pending.pop(key, None)
