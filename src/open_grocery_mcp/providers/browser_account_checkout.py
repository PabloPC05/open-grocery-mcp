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
from open_grocery_mcp.providers.browser_normalize import sanitize_url


class BrowserAccountCheckoutMixin:
    @classmethod
    def _public_checkout_value(cls, value: Any, *, key: str = "") -> Any:
        if isinstance(value, Mapping):
            return {
                str(child_key): cls._public_checkout_value(
                    child_value, key=str(child_key)
                )
                for child_key, child_value in value.items()
                if not str(child_key).startswith("_")
            }
        if isinstance(value, list):
            return [cls._public_checkout_value(item) for item in value]
        if key.casefold().endswith("url"):
            return sanitize_url(value)
        return value

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
            temporary: Path | None = None
            try:
                with NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.root, delete=False
                ) as handle:
                    temporary = Path(handle.name)
                    json.dump(records, handle, ensure_ascii=False, indent=2)
                # Set the mode while the file is still temporary.  A failed
                # replace must never leave checkout metadata readable by
                # another local user.
                self._protect(temporary)
                temporary.replace(self.checkout_path)
                self._protect(self.checkout_path)
            finally:
                # ``Path.replace`` can fail (for example on Windows when the
                # destination is held open).  Do not strand a sensitive temp
                # file in the account directory in that case.
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

    def _checkout_record(self, checkout_id: str) -> dict[str, Any]:
        record = self._read_checkout_records().get(checkout_id)
        if record is None or record.get("store") != self.config.key:
            raise InvalidRequest(f"unknown {self.config.label} checkout_id {checkout_id!r}")
        return record

    def remember_external_checkout(
        self,
        checkout_id: str,
        snapshot: Mapping[str, Any],
        *,
        backend: str,
    ) -> None:
        """Persist a non-browser checkout snapshot in the private local store.

        Hybrid providers use this after a verified HTTP checkout creation so a
        process restart does not accidentally reinterpret the retailer checkout
        id as a Playwright-only record.  Only the minimum normalized snapshot is
        retained; no token, cookie or private URL is accepted.
        """

        normalized_id = str(checkout_id).strip()
        normalized_backend = str(backend).strip()
        if not normalized_id or not normalized_backend:
            raise InvalidRequest("external checkout needs an id and backend")
        allowed = {
            "store",
            "checkout_id",
            "total",
            "total_text",
            "currency",
            "address_id",
            "slot_id",
            "slot_start",
            "slot_end",
            "delivery_date",
            "schedule_range_id",
            "order_placed",
            "checkout_present",
            "checkout_backend",
            "summary_prepared",
            "_reviewed_lines",
        }
        public_snapshot = {
            str(key): self._public_checkout_value(value, key=str(key))
            for key, value in snapshot.items()
            if str(key) in allowed
        }
        records = self._read_checkout_records()
        records[normalized_id] = {
            "store": self.config.key,
            "backend": normalized_backend,
            "created_at": datetime.now(UTC).isoformat(),
            "snapshot": public_snapshot,
        }
        self._write_checkout_records(records)
        self._active_checkout_id = normalized_id

    def external_checkout_snapshot(
        self,
        checkout_id: str,
        *,
        backend: str,
    ) -> dict[str, Any] | None:
        """Return a normalized locally persisted HTTP checkout, if present."""

        record = self._read_checkout_records().get(str(checkout_id))
        if (
            record is None
            or record.get("store") != self.config.key
            or record.get("backend") != backend
            or not isinstance(record.get("snapshot"), Mapping)
        ):
            return None
        self._active_checkout_id = str(checkout_id)
        return dict(record["snapshot"])

    def open_human_review(
        self,
        *,
        checkout_id: str | None = None,
        checkout_review: bool = False,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Open the safest known retailer review page and perform no clicks."""

        preferred_url: str | None = None
        if checkout_id:
            record = self._checkout_record(checkout_id)
            preferred_url = str(record.get("url") or "").strip() or None
        return self._driver().open_human_handoff(
            preferred_url=preferred_url,
            checkout_review=checkout_review,
            timeout_seconds=timeout_seconds,
        )

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
        checkout_url = (
            str(active[1].get("url") or "").strip() or None
            if active
            else None
        )
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
        public = self._public_checkout_value(checkout)
        return {**public, "store": self.config.key, "checkout_id": checkout_id}

    def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        record = self._checkout_record(checkout_id)
        self._active_checkout_id = checkout_id
        checkout = self._driver().checkout(str(record["url"]))
        if checkout.get("address_id") in (None, ""):
            checkout["address_id"] = record.get("address_id")
        if checkout.get("slot_id") in (None, ""):
            checkout["slot_id"] = record.get("slot_id")
        public = self._public_checkout_value(checkout)
        return {**public, "store": self.config.key, "checkout_id": checkout_id}

    def set_checkout_delivery(
        self,
        checkout_id: str,
        *,
        address_id: str | int,
        slot_id: str,
        max_total: Decimal,
    ) -> dict[str, Any]:
        record = self._checkout_record(checkout_id)
        if max_total <= 0:
            raise InvalidRequest("max_total must be greater than zero")
        before = self._driver().checkout(str(record["url"]))
        before_total = as_decimal(before.get("total"))
        if before_total <= 0 or before_total > max_total:
            raise BudgetExceeded(
                f"{self.config.label} checkout total {money(before_total)} EUR is "
                f"outside cap {money(max_total)} EUR before delivery selection"
            )
        result = self._driver().set_delivery(
            str(record["url"]),
            address_id=address_id,
            slot_id=slot_id,
        )
        total = as_decimal(result.get("total"))
        selection_matches = (
            str(result.get("address_id") or "") == str(address_id)
            and str(result.get("slot_id") or "") == str(slot_id)
        )
        if total <= 0 or total > max_total or not selection_matches:
            previous_address = before.get("address_id") or record.get("address_id")
            previous_slot = before.get("slot_id") or record.get("slot_id")
            if previous_address not in (None, "") and previous_slot not in (None, ""):
                try:
                    restored = self._driver().set_delivery(
                        str(result.get("_private_url") or record["url"]),
                        address_id=previous_address,
                        slot_id=str(previous_slot),
                    )
                    if (
                        as_decimal(restored.get("total")) != before_total
                        or str(restored.get("address_id") or "")
                        != str(previous_address)
                        or str(restored.get("slot_id") or "")
                        != str(previous_slot)
                    ):
                        raise ProviderError("delivery rollback state mismatch")
                except Exception as rollback_error:
                    raise ProviderError(
                        f"{self.config.label} delivery selection was invalid and "
                        "rollback could not be verified; inspect checkout before retrying"
                    ) from rollback_error
            else:
                raise ProviderError(
                    f"{self.config.label} delivery selection changed checkout to an "
                    "invalid state and no previous selection can be restored; inspect "
                    "checkout before any further write"
                )
            if total <= 0 or total > max_total:
                raise BudgetExceeded(
                    f"{self.config.label} checkout total {money(total)} EUR is outside "
                    f"cap {money(max_total)} EUR after delivery selection"
                )
            raise ProviderError(
                f"{self.config.label} did not confirm the reviewed delivery selection; "
                "the previous selection was restored"
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
        public = self._public_checkout_value(result)
        return {**public, "store": self.config.key, "checkout_id": checkout_id}

    def _claim_submission_attempt(self, checkout_id: str) -> dict[str, Any]:
        """Atomically reserve the one irreversible submission for a checkout."""

        with self._lock:
            record = self._checkout_record(checkout_id)
            if record.get("submission_attempted_at"):
                raise InvalidRequest(
                    "an order submission was already attempted for this checkout; check the "
                    "retailer order history before doing anything else. Automatic retries are refused"
                )
            records = self._read_checkout_records()
            claimed = {
                **record,
                "submission_attempted_at": datetime.now(UTC).isoformat(),
                "submission_status": "in_progress",
            }
            records[checkout_id] = claimed
            self._write_checkout_records(records)
            return claimed

    def submit_order(self, checkout_id: str, *, max_total: Decimal) -> dict[str, Any]:
        if os.getenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", "").casefold() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise OrderSubmissionDisabled(
                "retailer writes are disabled for browser order submission"
            )
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
        if os.getenv(
            "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", ""
        ).casefold() not in {"1", "true", "yes", "on"}:
            raise OrderSubmissionDisabled(
                "browser order submission is disabled; set "
                "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1 only for a "
                "verified local browser flow"
            )
        record = self._checkout_record(checkout_id)
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

        # Persist the attempt before the irreversible click. The claim is
        # atomic under the account lock, so concurrent callers cannot both
        # reach the browser submit control. If the browser or network fails
        # afterwards, the MCP refuses to retry automatically and directs the
        # user to inspect retailer order history.
        record = self._claim_submission_attempt(checkout_id)
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
        return {
            **self._public_checkout_value(result),
            "checkout_id": checkout_id,
        }

    def close(self) -> None:
        """No long-lived browser process is kept open."""
