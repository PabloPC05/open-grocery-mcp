"""Mercadona delivery, checkout and gated order submission."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Mapping
from urllib.parse import quote

from open_grocery_mcp.errors import BudgetExceeded, ConcurrentCartChange, InvalidRequest, OrderSubmissionDisabled, ProviderError
from open_grocery_mcp.models import as_decimal, money


class MercadonaCheckoutMixin:

    def addresses(self) -> list[dict[str, Any]]:
        payload, _ = self._request('GET', f'/customers/{self._customer_id()}/addresses/')
        rows = payload.get('results', []) if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list):
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            result.append({'id': row.get('id'), 'label': row.get('alias') or row.get('name') or 'Dirección guardada', 'postal_code': row.get('postal_code') or row.get('zip_code'), 'city': row.get('city') or row.get('locality') or row.get('town'), 'is_default': bool(row.get('is_default') or row.get('default')), 'full_street_redacted': True})
        return result

    def slots(self, address_id: str | int) -> list[dict[str, Any]]:
        encoded = quote(str(address_id), safe='')
        payload, _ = self._request('GET', f'/customers/{self._customer_id()}/addresses/{encoded}/slots/')
        rows = payload.get('results', []) if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list):
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            result.append({'id': str(row.get('id', '')), 'start': row.get('start') or row.get('start_date'), 'end': row.get('end') or row.get('end_date'), 'price': float(as_decimal(row.get('price'))), 'price_text': money(as_decimal(row.get('price'))), 'available': bool(row.get('available', True)), 'open': bool(row.get('open', True))})
        return result

    def preview_checkout(self, *, expected_version: int | None, max_total: Decimal) -> dict[str, Any]:
        if max_total <= 0:
            raise InvalidRequest('max_total must be greater than zero')
        raw = self._cart_raw()
        cart = self._normalize_cart(raw)
        version = int(cart['version'])
        if expected_version is not None and version != expected_version:
            raise ConcurrentCartChange(f'Mercadona cart version is {version}, not reviewed version {expected_version}')
        total = as_decimal(cart['total'])
        if total > max_total:
            raise BudgetExceeded(f'Mercadona cart total {money(total)} EUR exceeds cap {money(max_total)} EUR')
        return {'store': 'mercadona', 'cart': cart, 'cart_payload': {'id': str(raw.get('id', '')), 'version': int(raw.get('version') or 0), 'lines': self._write_lines(raw)}, 'max_total': float(max_total), 'max_total_text': money(max_total), 'checkout_created': False, 'order_placed': False}

    @staticmethod
    def _extract_total(payload: Mapping[str, Any]) -> Decimal:
        summary = payload.get('summary', {})
        if isinstance(summary, Mapping):
            total = as_decimal(summary.get('total'))
            if total > 0:
                return total
        total = as_decimal(payload.get('total'))
        if total > 0:
            return total
        price = payload.get('price')
        if isinstance(price, Mapping):
            total = as_decimal(price.get('total'))
            if total > 0:
                return total
        return Decimal('0')

    @classmethod
    def _normalize_checkout(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        total = cls._extract_total(payload)
        address = payload.get('address', {})
        slot = payload.get('slot', {})
        return {'store': 'mercadona', 'checkout_id': str(payload.get('id') or payload.get('checkout_id') or ''), 'total': float(total), 'total_text': money(total), 'currency': 'EUR', 'address_id': address.get('id') if isinstance(address, Mapping) else None, 'slot_id': slot.get('id') if isinstance(slot, Mapping) else None, 'slot_start': slot.get('start') if isinstance(slot, Mapping) else None, 'slot_end': slot.get('end') if isinstance(slot, Mapping) else None, 'order_placed': False}

    def create_checkout(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        max_total = as_decimal(plan.get('max_total'))
        expected = plan.get('cart', {})
        current_raw = self._cart_raw()
        current = self._normalize_cart(current_raw)
        if int(current['version']) != int(expected.get('version') or -1):
            raise ConcurrentCartChange('Mercadona cart changed after checkout review')
        if as_decimal(current['total']) > max_total:
            raise BudgetExceeded('Mercadona cart now exceeds the approved checkout cap')
        payload, _ = self._request('POST', f'/customers/{self._customer_id()}/checkouts/', json_body={'cart': plan.get('cart_payload', {})})
        if not isinstance(payload, Mapping):
            raise ProviderError('Mercadona checkout creation returned an invalid response')
        normalized = self._normalize_checkout(payload)
        if as_decimal(normalized['total']) > max_total:
            raise BudgetExceeded('Mercadona checkout total exceeds the approved cap')
        normalized['checkout_created'] = True
        return normalized

    def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        encoded = quote(checkout_id, safe='')
        payload, _ = self._request('GET', f'/customers/{self._customer_id()}/checkouts/{encoded}/')
        if not isinstance(payload, Mapping):
            raise ProviderError('Mercadona checkout response was invalid')
        return self._normalize_checkout(payload)

    def set_checkout_delivery(self, checkout_id: str, *, address_id: str | int, slot_id: str, max_total: Decimal) -> dict[str, Any]:
        encoded = quote(checkout_id, safe='')
        payload, _ = self._request('PUT', f'/customers/{self._customer_id()}/checkouts/{encoded}/delivery-info/', json_body={'address': {'id': address_id}, 'slot': {'id': slot_id}})
        if not isinstance(payload, Mapping):
            payload = {}
        normalized = self._normalize_checkout(payload)
        if not normalized['checkout_id']:
            normalized = self.get_checkout(checkout_id)
        total = as_decimal(normalized['total'])
        if total <= 0:
            normalized = self.get_checkout(checkout_id)
            total = as_decimal(normalized['total'])
        if total <= 0:
            raise BudgetExceeded('could not verify checkout total after selecting delivery')
        if total > max_total:
            raise BudgetExceeded(f'checkout total {money(total)} EUR exceeds cap {money(max_total)} EUR')
        normalized['delivery_updated'] = True
        return normalized

    def submit_order(self, checkout_id: str, *, max_total: Decimal) -> dict[str, Any]:
        enabled = os.getenv('OPEN_GROCERY_ENABLE_ORDER_SUBMISSION', '').casefold() in {'1', 'true', 'yes', 'on'}
        if not enabled:
            raise OrderSubmissionDisabled("order submission is disabled; set OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1 only on the user's own local machine")
        checkout = self.get_checkout(checkout_id)
        total = as_decimal(checkout.get('total'))
        if total <= 0:
            raise BudgetExceeded('could not verify an authoritative checkout total; refusing')
        if total > max_total:
            raise BudgetExceeded(f'checkout total {money(total)} EUR exceeds cap {money(max_total)} EUR')
        encoded = quote(checkout_id, safe='')
        payload, _ = self._request('POST', f'/customers/{self._customer_id()}/checkouts/{encoded}/orders/')
        result = dict(payload) if isinstance(payload, Mapping) else {'response': payload}
        result.update({'store': 'mercadona', 'checkout_id': checkout_id, 'authorized_total': float(total), 'authorized_total_text': money(total), 'order_placed': True})
        return result
