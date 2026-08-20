"""Address, slot, checkout and final-submit browser operations."""

from __future__ import annotations

import os
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from open_grocery_mcp.errors import InvalidRequest, ProviderError
from open_grocery_mcp.models import money
from open_grocery_mcp.providers.browser_normalize import (
    extract_order_id,
    normalize_addresses,
    normalize_slots,
    normalized_text,
    parse_money_text,
)
from open_grocery_mcp.providers.browser_scripts import DOM_OPTIONS_SCRIPT


class BrowserDriverCheckoutMixin:
    @staticmethod
    def _dom_options(page: Any, kind: str) -> list[dict[str, Any]]:
        value = page.evaluate(DOM_OPTIONS_SCRIPT, kind)
        return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    def addresses(self, checkout_url: str | None = None) -> list[dict[str, Any]]:
        with self._page() as (page, captured, _):
            if checkout_url:
                page.goto(self._retailer_url(checkout_url), wait_until="domcontentloaded")
            else:
                self._goto_account(page)
            self._click_patterns(page, (r"direcciones", r"domicilios", r"addresses"), required=False)
            page.wait_for_timeout(700)
            for payload in reversed(captured):
                addresses = normalize_addresses(payload)
                if addresses:
                    return addresses
            options = self._dom_options(page, "address")
            result: list[dict[str, Any]] = []
            for item in options:
                label = str(item.get("label") or "")
                postal_match = re.search(r"\b\d{5}\b", label)
                postal_code = postal_match.group(0) if postal_match else ""
                result.append(
                    {
                        "id": item["id"],
                        "label": (
                            f"{postal_code} · Dirección guardada"
                            if postal_code
                            else "Dirección guardada"
                        ),
                        "postal_code": postal_code,
                        "street_redacted": True,
                        "default": item.get("checked", False),
                    }
                )
            return result

    def _select_option(self, page: Any, kind: str, option_id: str) -> None:
        escaped = option_id.replace('"', '')
        selectors = (
            f'input[type="radio"][value="{escaped}"]',
            f'option[value="{escaped}"]',
            f'[data-id="{escaped}"]',
            f'[data-value="{escaped}"]',
        )
        for selector in selectors:
            try:
                target = page.locator(selector)
                if not target.count():
                    continue
                first = target.first
                tag = first.evaluate("node => node.tagName.toLowerCase()")
                if tag == "option":
                    first.locator("xpath=..").select_option(value=option_id)
                elif tag == "input":
                    first.check()
                else:
                    first.click()
                page.wait_for_timeout(400)
                return
            except Exception:
                continue
        options = self._dom_options(page, kind)
        selected = next((item for item in options if str(item.get("id")) == str(option_id)), None)
        if selected:
            expression = re.compile(re.escape(str(selected.get("label", ""))[:80]), re.I)
            target = page.get_by_text(expression).first
            if target.count():
                target.click()
                page.wait_for_timeout(400)
                return
        raise ProviderError(f"could not select {kind} {option_id!r} on {self.config.label}")

    def slots(self, address_id: str | int, checkout_url: str | None = None) -> list[dict[str, Any]]:
        with self._page() as (page, captured, _):
            if checkout_url:
                page.goto(self._retailer_url(checkout_url), wait_until="domcontentloaded")
            else:
                self._goto_checkout(page)
            try:
                self._select_option(page, "address", str(address_id))
            except ProviderError:
                # Some sites bind the only/default address automatically.
                pass
            self._click_patterns(page, self.config.continue_patterns, required=False)
            page.wait_for_timeout(700)
            for payload in reversed(captured):
                slots = normalize_slots(payload)
                if slots:
                    return slots
            options = self._dom_options(page, "slot")
            return [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "start": "",
                    "end": "",
                    "available": not item.get("disabled", False),
                    "open": not item.get("disabled", False),
                    "price": float(parse_money_text(item["label"])),
                    "price_text": money(parse_money_text(item["label"])),
                }
                for item in options
            ]

    @staticmethod
    def _safe_page_url(page: Any) -> str:
        parts = urlsplit(page.url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _private_page_url(page: Any) -> str:
        # Stored only in the owner-readable local checkout file; never exposed by MCP.
        parts = urlsplit(page.url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))

    def create_checkout(self) -> dict[str, Any]:
        with self._page() as (page, captured, _):
            self._goto_checkout(page)
            page.wait_for_timeout(900)
            cart = self._captured_cart(captured) or self._dom_cart(page)
            return {
                "url": self._safe_page_url(page),
                "_private_url": self._private_page_url(page),
                "cart": cart,
                "total": cart["total"],
                "total_text": cart["total_text"],
                "address_id": None,
                "slot_id": None,
                "state_changed": True,
                "order_placed": False,
            }

    def checkout(self, checkout_url: str) -> dict[str, Any]:
        with self._page() as (page, captured, _):
            page.goto(self._retailer_url(checkout_url), wait_until="domcontentloaded")
            page.wait_for_timeout(700)
            cart = self._captured_cart(captured) or self._dom_cart(page)
            address_options = self._dom_options(page, "address")
            slot_options = self._dom_options(page, "slot")
            selected_address = next((item for item in address_options if item.get("checked")), None)
            selected_slot = next((item for item in slot_options if item.get("checked")), None)
            return {
                "url": self._safe_page_url(page),
                "total": cart["total"],
                "total_text": cart["total_text"],
                "currency": "EUR",
                "cart_version": cart["version"],
                "address_id": selected_address.get("id") if selected_address else None,
                "slot_id": selected_slot.get("id") if selected_slot else None,
                "order_placed": False,
            }

    def set_delivery(
        self,
        checkout_url: str,
        *,
        address_id: str | int,
        slot_id: str,
    ) -> dict[str, Any]:
        with self._page() as (page, captured, _):
            page.goto(self._retailer_url(checkout_url), wait_until="domcontentloaded")
            self._select_option(page, "address", str(address_id))
            self._click_patterns(page, self.config.continue_patterns, required=False)
            page.wait_for_timeout(450)
            self._select_option(page, "slot", str(slot_id))
            self._click_patterns(page, self.config.continue_patterns, required=False)
            page.wait_for_timeout(800)
            cart = self._captured_cart(captured) or self._dom_cart(page)
            return {
                "url": self._safe_page_url(page),
                "_private_url": self._private_page_url(page),
                "total": cart["total"],
                "total_text": cart["total_text"],
                "currency": "EUR",
                "cart_version": cart["version"],
                "address_id": str(address_id),
                "slot_id": str(slot_id),
                "order_placed": False,
                "state_changed": True,
            }

    def submit(self, checkout_url: str) -> dict[str, Any]:
        if os.getenv("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", "").casefold() not in {
            "1", "true", "yes", "on"
        }:
            raise InvalidRequest(
                "browser-driven order submission is disabled; set "
                "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1 locally"
            )
        submit_headless = os.getenv("OPEN_GROCERY_BROWSER_SUBMIT_HEADLESS", "").casefold() in {
            "1", "true", "yes", "on"
        }
        with self._page(headless=submit_headless) as (page, captured, _):
            page.goto(self._retailer_url(checkout_url), wait_until="domcontentloaded")
            before = self._dom_cart(page)
            self._click_patterns(page, self.config.submit_patterns, roles=("button",), required=True)
            page.wait_for_timeout(1500)
            text = normalized_text(page.locator("body").inner_text())
            success = any(re.search(pattern, text, re.I) for pattern in self.config.success_patterns)
            payment = any(re.search(pattern, text, re.I) for pattern in self.config.payment_patterns)
            order_id = None
            for payload in reversed(captured):
                order_id = extract_order_id(payload)
                if order_id:
                    break
            verified = bool(success or order_id)
            return {
                "store": self.config.key,
                # None means a submission click occurred but the browser could not
                # prove whether the retailer accepted it. A caller must check order
                # history and must not retry automatically.
                "order_placed": True if verified else None,
                "submission_attempted": True,
                "order_id": order_id,
                "requires_user_action": bool(payment and not verified),
                "requires_manual_verification": bool(not verified and not payment),
                "status": (
                    "confirmed"
                    if verified
                    else "payment_or_confirmation_pending"
                    if payment
                    else "submission_result_unverified"
                ),
                "total": before["total"],
                "total_text": before["total_text"],
                "page_url": self._safe_page_url(page),
            }
