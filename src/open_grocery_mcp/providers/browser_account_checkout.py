"""Delivery, checkout and final-submit handling for browser retailers."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from uuid import uuid4

from open_grocery_mcp.errors import (
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    OrderSubmissionDisabled,
    ProviderError,
)
from open_grocery_mcp.models import as_decimal, money


class BrowserAccountCheckoutMixin:
    def _read_checkout_records(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if not self.checkout_path.exists():
                return {}
            try:
                payload = json.loads(self.checkout_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        if not isinstance(payload, Mapping):
            return {}
        return {
            str(key): dict(value)
            for key, value in payload.items()
            if isinstance(value, Mapping)
        }

    def _write_checkout_records(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        with self._lock:
            self._protect_root()
            with NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False) as handle:
                json.dump(records, handle, ensure_ascii=False, indent=2)
                temporary = Path(handle.name)
            temporary.replace(self.checkout_path)
            self._protect(self.checkout_path)

    def _checkout_record(self, checkout_id: str) -> dict[str, Any]:
        record = self._read_checkout_records().get(checkout_id)
        if record is None or record.get("store") != self.config.key:
            raise InvalidRequest(f"unknown {self.config.label} checkout_id {checkout_id!r}")
        return record

    def _active_checkout_record(self) -> tuple[str, dict[str, Any]] | None:
        records = self._read_checkout_records()
        if self._active_checkout_id and self._active_checkout_id in records:
            return self._active_checkout_id, records[self._active_checkout_id]
        if not records:
            return None
        checkout_id = next(reversed(records))
        return checkout_id, records[checkout_id]

    def addresses(self) -> list[dict[str, Any]]:
        active = self._active_checkout_record()
        checkout_url = str(active[1].get("url")) if active else None
        return self._driver().addresses(checkout_url=checkout_url)

    def slots(self, address_id: str | int) -> list[dict[str, Any]]:
        active = self._active_checkout_record()
        if active is None:
            raise InvalidRequest(
                f"create a confirmed {self.config.label} checkout before listing delivery slots"
            )
        return self._driver().slots(address_id, checkout_url=str(active[1]["url"]))

    def preview_checkout(
        self,
        *,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        if max_total <= 0:
            raise InvalidRequest("max_total must be greater than zero")
        cart = self.cart()
        version = int(cart.get("version") or 0)
        if expected_version is not None and version != expected_version:
            raise ConcurrentCartChange(
                f"{self.config.label} cart changed after review"
            )
        total = as_decimal(cart.get("total"))
        if total <= 0:
            raise InvalidRequest("cart is empty or has no verifiable positive total")
        if total > max_total:
            raise BudgetExceeded(
                f"{self.config.label} cart total {money(total)} EUR exceeds cap {money(max_total)} EUR"
            )
        return {
            "store": self.config.key,
            "expected_cart_version": version,
            "max_total": float(max_total),
            "max_total_text": money(max_total),
            "cart": cart,
            "state_changed": False,
            "browser_driven": True,
        }

    def create_checkout(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        current = self.cart()
        if int(current.get("version") or 0) != int(plan.get("expected_cart_version") or 0):
            raise ConcurrentCartChange(
                f"{self.config.label} cart changed before checkout creation"
            )
        cap = as_decimal(plan.get("max_total"))
        checkout = self._driver().create_checkout()
        total = as_decimal(checkout.get("total"))
        if total <= 0:
            raise BudgetExceeded(
                f"could not verify a positive {self.config.label} checkout total"
            )
        if total > cap:
            raise BudgetExceeded(
                f"{self.config.label} checkout total {money(total)} EUR exceeds cap {money(cap)} EUR"
            )
        checkout_id = f"{self.config.key}-{uuid4().hex}"
        records = self._read_checkout_records()
        records[checkout_id] = {
            "store": self.config.key,
            "url": str(checkout.get("_private_url") or checkout.get("url") or ""),
            "created_at": datetime.now(UTC).isoformat(),
            "address_id": checkout.get("address_id"),
            "slot_id": checkout.get("slot_id"),
        }
        self._write_checkout_records(records)
        self._active_checkout_id = checkout_id
        public = {key: value for key, value in checkout.items() if not key.startswith("_")}
        return {**public, "store": self.config.key, "checkout_id": checkout_id}

    def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        record = self._checkout_record(checkout_id)
        self._active_checkout_id = checkout_id
        checkout = self._driver().checkout(str(record["url"]))
        if checkout.get("address_id") in (None, ""):
            checkout["address_id"] = record.get("address_id")
        if checkout.get("slot_id") in (None, ""):
            checkout["slot_id"] = record.get("slot_id")
        return {**checkout, "store": self.config.key, "checkout_id": checkout_id}

    def set_checkout_delivery(
        self,
        checkout_id: str,
        *,
        address_id: str | int,
        slot_id: str,
        max_total: Decimal,
    ) -> dict[str, Any]:
        record = self._checkout_record(checkout_id)
        result = self._driver().set_delivery(
            str(record["url"]),
            address_id=address_id,
            slot_id=slot_id,
        )
        total = as_decimal(result.get("total"))
        if total <= 0:
            raise BudgetExceeded(
                f"could not verify a positive {self.config.label} checkout total after delivery selection"
            )
        if total > max_total:
            raise BudgetExceeded(
                f"{self.config.label} checkout total {money(total)} EUR exceeds cap {money(max_total)} EUR"
            )
        records = self._read_checkout_records()
        records[checkout_id] = {
            **record,
            "url": str(result.get("_private_url") or result.get("url") or record["url"]),
            "address_id": str(address_id),
            "slot_id": str(slot_id),
        }
        self._write_checkout_records(records)
        self._active_checkout_id = checkout_id
        public = {key: value for key, value in result.items() if not key.startswith("_")}
        return {**public, "store": self.config.key, "checkout_id": checkout_id}

    def submit_order(self, checkout_id: str, *, max_total: Decimal) -> dict[str, Any]:
        if os.getenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "").casefold() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise OrderSubmissionDisabled(
                "order submission is disabled; set OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1 "
                "only on the user's own local machine"
            )
        record = self._checkout_record(checkout_id)
        if record.get("submission_attempted_at"):
            raise InvalidRequest(
                "an order submission was already attempted for this checkout; check the "
                "retailer order history before doing anything else. Automatic retries are refused"
            )
        checkout = self.get_checkout(checkout_id)
        total = as_decimal(checkout.get("total"))
        if total <= 0 or total > max_total:
            raise BudgetExceeded(
                f"{self.config.label} checkout total {money(total)} EUR is outside the "
                f"authorized cap {money(max_total)} EUR"
            )
        if checkout.get("address_id") in (None, "") or checkout.get("slot_id") in (None, ""):
            raise InvalidRequest(
                "checkout needs a delivery address and slot before order submission"
            )

        # Persist the attempt before the irreversible click. If the browser or
        # network fails afterwards, the MCP refuses to retry automatically and
        # directs the user to inspect retailer order history.
        records = self._read_checkout_records()
        records[checkout_id] = {
            **record,
            "submission_attempted_at": datetime.now(UTC).isoformat(),
            "submission_status": "in_progress",
        }
        self._write_checkout_records(records)
        try:
            result = self._driver().submit(str(record["url"]))
        except Exception as exc:
            records = self._read_checkout_records()
            records[checkout_id] = {
                **records.get(checkout_id, record),
                "submission_status": "result_unverified",
            }
            self._write_checkout_records(records)
            raise ProviderError(
                "order submission was attempted but its result could not be verified; "
                "check retailer order history and do not retry automatically"
            ) from exc

        records = self._read_checkout_records()
        records[checkout_id] = {
            **records.get(checkout_id, record),
            "submission_status": str(result.get("status") or "unverified"),
            "order_id": result.get("order_id"),
            "order_placed": result.get("order_placed"),
        }
        self._write_checkout_records(records)
        return {**result, "checkout_id": checkout_id}

    def close(self) -> None:
        """No long-lived browser process is kept open."""
