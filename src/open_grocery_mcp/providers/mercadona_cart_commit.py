"""Verified Mercadona cart commits and rollback."""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from open_grocery_mcp.errors import BudgetExceeded, ConcurrentCartChange, ProviderError
from open_grocery_mcp.models import as_decimal, money


class MercadonaCartCommitMixin:

    @staticmethod
    def _line_signature(lines: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
        normalized: list[tuple[str, str]] = []
        for line in lines:
            product_id = str(line.get('product_id', '')).strip()
            quantity = as_decimal(line.get('quantity'))
            if product_id and quantity > 0:
                normalized.append((product_id, str(quantity.normalize())))
        return tuple(sorted(normalized))

    def _wait_for_cart_lines(self, expected_lines: Sequence[Mapping[str, Any]], *, timeout_seconds: float = 8.0) -> dict[str, Any]:
        expected = self._line_signature(expected_lines)
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self._cart_raw()
            if self._line_signature(self._write_lines(last)) == expected:
                return last
            time.sleep(0.25)
        raise ProviderError('Mercadona cart did not reach the reviewed state before the verification timeout')

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        max_total = as_decimal(plan.get('max_total'))
        expected_version = int(plan.get('expected_cart_version') or 0)
        current = self._cart_raw()
        current_version = int(current.get('version') or 0)
        if current_version != expected_version:
            raise ConcurrentCartChange(f'Mercadona cart changed from version {expected_version} to {current_version}; review again')
        body = {'id': str(current.get('id') or plan.get('cart_id') or ''), 'lines': list(plan.get('desired_lines', []))}
        self._request('PUT', f'/customers/{self._customer_id()}/cart/', json_body=body, params=self._params())
        updated = self._wait_for_cart_lines(list(plan.get('desired_lines', [])))
        normalized = self._normalize_cart(updated)
        actual_total = as_decimal(normalized.get('total'))
        if actual_total > max_total:
            rollback_body = {'id': str(updated.get('id') or body['id']), 'lines': list(plan.get('previous_lines', []))}
            try:
                self._request('PUT', f'/customers/{self._customer_id()}/cart/', json_body=rollback_body, params=self._params())
                self._wait_for_cart_lines(list(plan.get('previous_lines', [])))
            except Exception as rollback_error:
                raise BudgetExceeded(f'cart exceeded the cap and automatic rollback failed: {rollback_error}') from rollback_error
            raise BudgetExceeded(f'Mercadona returned cart total {money(actual_total)} EUR above cap; previous cart restored')
        normalized['retailer_cart_modified'] = True
        normalized['order_placed'] = False
        return normalized
