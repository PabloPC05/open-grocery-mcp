"""Mercadona real-cart reads and update planning."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from open_grocery_mcp.errors import BudgetExceeded, ConcurrentCartChange, InvalidRequest, ProviderError
from open_grocery_mcp.models import as_decimal, money


class MercadonaCartMixin:

    def _cart_raw(self) -> dict[str, Any]:
        payload, _ = self._request('GET', f'/customers/{self._customer_id()}/cart/', params=self._params())
        if not isinstance(payload, dict):
            raise ProviderError('Mercadona cart response was not an object')
        return payload

    @staticmethod
    def _raw_lines(cart: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        lines = cart.get('lines', [])
        return [line for line in lines if isinstance(line, Mapping)] if isinstance(lines, list) else []

    @classmethod
    def _write_lines(cls, cart: Mapping[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for line in cls._raw_lines(cart):
            product = line.get('product') if isinstance(line.get('product'), Mapping) else {}
            product_id = str(line.get('product_id') or product.get('id') or '').strip()
            quantity = as_decimal(line.get('quantity'))
            if not product_id or quantity <= 0:
                continue
            sources = line.get('sources')
            result.append({'product_id': product_id, 'quantity': float(quantity), 'sources': list(sources) if isinstance(sources, list) else []})
        return result

    @classmethod
    def _normalize_cart(cls, cart: Mapping[str, Any]) -> dict[str, Any]:
        total = as_decimal(cart.get('summary', {}).get('total') if isinstance(cart.get('summary'), Mapping) else cart.get('total'))
        normalized: list[dict[str, Any]] = []
        for line in cls._raw_lines(cart):
            product = line.get('product') if isinstance(line.get('product'), Mapping) else {}
            product_id = str(line.get('product_id') or product.get('id') or '').strip()
            pricing = product.get('price_instructions', {})
            if not isinstance(pricing, Mapping):
                pricing = {}
            quantity = as_decimal(line.get('quantity'))
            unit_price = as_decimal(pricing.get('unit_price'))
            normalized.append({'product_id': product_id, 'name': str(product.get('display_name') or line.get('display_name') or ''), 'quantity': float(quantity), 'unit_price': float(unit_price), 'unit_price_text': money(unit_price), 'line_total': float(unit_price * quantity), 'line_total_text': money(unit_price * quantity), 'sources': line.get('sources') if isinstance(line.get('sources'), list) else []})
        return {'store': 'mercadona', 'cart_id': str(cart.get('id', '')), 'version': int(cart.get('version') or 0), 'products_count': int(cart.get('products_count') or len(normalized)), 'total': float(total), 'total_text': money(total), 'currency': 'EUR', 'lines': normalized}

    def cart(self) -> dict[str, Any]:
        return self._normalize_cart(self._cart_raw())

    def _product_unit_price(self, product_id: str) -> Decimal:
        payload, _ = self._request('GET', f"/products/{quote(product_id, safe='')}/", params=self._params())
        if not isinstance(payload, Mapping):
            raise ProviderError(f'Mercadona product {product_id!r} returned no detail')
        pricing = payload.get('price_instructions', {})
        price = as_decimal(pricing.get('unit_price') if isinstance(pricing, Mapping) else None)
        if price <= 0:
            raise ProviderError(f'Mercadona returned no usable price for product {product_id!r}')
        return price

    def preview_cart_update(self, changes: Sequence[Mapping[str, Any]], *, mode: str, expected_version: int | None, max_total: Decimal) -> dict[str, Any]:
        if mode not in {'merge', 'replace'}:
            raise InvalidRequest("cart update mode must be 'merge' or 'replace'")
        if max_total <= 0:
            raise InvalidRequest('max_total must be greater than zero')
        cart = self._cart_raw()
        version = int(cart.get('version') or 0)
        if expected_version is not None and version != expected_version:
            raise ConcurrentCartChange(f'Mercadona cart version is {version}, not reviewed version {expected_version}')
        previous_lines = self._write_lines(cart)
        current = {line['product_id']: dict(line) for line in previous_lines}
        desired: dict[str, dict[str, Any]] = {} if mode == 'replace' else current
        for change in changes:
            product_id = str(change.get('product_id', '')).strip()
            quantity = as_decimal(change.get('quantity'))
            if not product_id:
                raise InvalidRequest('every cart change needs product_id')
            if quantity <= 0:
                desired.pop(product_id, None)
                continue
            desired[product_id] = {'product_id': product_id, 'quantity': float(quantity), 'sources': list(change.get('sources', [])) if isinstance(change.get('sources', []), list) else []}
        current_prices: dict[str, Decimal] = {}
        for line in self._raw_lines(cart):
            product = line.get('product') if isinstance(line.get('product'), Mapping) else {}
            product_id = str(line.get('product_id') or product.get('id') or '')
            pricing = product.get('price_instructions', {})
            if isinstance(pricing, Mapping):
                price = as_decimal(pricing.get('unit_price'))
                if product_id and price > 0:
                    current_prices[product_id] = price
        total = Decimal('0')
        public_lines: list[dict[str, Any]] = []
        for product_id, line in desired.items():
            price = current_prices.get(product_id) or self._product_unit_price(product_id)
            quantity = as_decimal(line['quantity'])
            total += price * quantity
            public_lines.append({'product_id': product_id, 'quantity': float(quantity), 'unit_price': float(price), 'unit_price_text': money(price), 'line_total': float(price * quantity), 'line_total_text': money(price * quantity)})
        if total > max_total:
            raise BudgetExceeded(f'proposed Mercadona cart total {money(total)} EUR exceeds cap {money(max_total)} EUR')
        return {'store': 'mercadona', 'cart_id': str(cart.get('id', '')), 'expected_cart_version': version, 'mode': mode, 'max_total': float(max_total), 'max_total_text': money(max_total), 'estimated_total': float(total), 'estimated_total_text': money(total), 'currency': 'EUR', 'lines': public_lines, 'desired_lines': list(desired.values()), 'previous_lines': previous_lines, 'retailer_cart_modified': False}
