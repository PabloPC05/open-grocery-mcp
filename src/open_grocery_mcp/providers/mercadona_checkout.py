"""Mercadona delivery, checkout and gated order submission."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from urllib.parse import quote

from open_grocery_mcp.errors import AuthenticationRequired, BudgetExceeded, ConcurrentCartChange, InvalidRequest, OrderSubmissionDisabled, ProviderError
from open_grocery_mcp.models import as_decimal, money


class MercadonaCheckoutMixin:

    _SLOTS_PAGE_SIZE = 100

    def _order_attempt_path(self):
        return self.state_path.parent / 'order_attempts.json'

    def _mark_order_attempt(self, checkout_id: str) -> None:
        key = hashlib.sha256(checkout_id.encode('utf-8')).hexdigest()
        path = self._order_attempt_path()
        with self._lock:
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                payload = []
            attempts = {str(item) for item in payload if isinstance(item, str)}
            if key in attempts:
                raise InvalidRequest(
                    'an order submission was already attempted for this checkout; '
                    'inspect retailer order history before any further action'
                )
            attempts.add(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with NamedTemporaryFile(
                    'w',
                    encoding='utf-8',
                    dir=path.parent,
                    prefix=f'.{path.name}.',
                    suffix='.tmp',
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    json.dump(sorted(attempts), handle, separators=(',', ':'))
                try:
                    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
                temporary.replace(path)
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

    @staticmethod
    def _collection_rows(payload: Any) -> list[Mapping[str, Any]]:
        rows = payload.get('results') if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list):
            raise ProviderError('Mercadona collection response was not a list')
        return [row for row in rows if isinstance(row, Mapping)]

    @staticmethod
    def _delivery_address_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        """Mirror the current storefront's ``Ase`` address serializer."""

        keys = (
            'id',
            'address',
            'address_detail',
            'comments',
            'entered_manually',
            'latitude',
            'longitude',
            'permanent_address',
            'postal_code',
            'town',
        )
        payload = {key: row[key] for key in keys if key in row}
        if not str(payload.get('id') or '').strip():
            raise ProviderError('Mercadona delivery address had no stable id')
        return payload

    @staticmethod
    def _delivery_slot_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        """Mirror the current storefront's ``yx`` slot serializer."""

        keys = (
            'id',
            'start',
            'end',
            'available',
            'open',
            'price',
            'cutoff_time',
            'timezone',
        )
        payload = {key: row[key] for key in keys if key in row}
        if not str(payload.get('id') or '').strip():
            raise ProviderError('Mercadona delivery slot had no stable id')
        return payload

    def _address_rows(self) -> list[Mapping[str, Any]]:
        payload, _ = self._request(
            'GET',
            f'/customers/{self._customer_id()}/addresses/',
            params=self._params(),
        )
        return self._collection_rows(payload)

    def addresses(self) -> list[dict[str, Any]]:
        rows = self._address_rows()
        if not isinstance(rows, list):
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            result.append({'id': row.get('id'), 'label': row.get('alias') or row.get('name') or 'Dirección guardada', 'postal_code': row.get('postal_code') or row.get('zip_code'), 'city': row.get('city') or row.get('locality') or row.get('town'), 'is_default': bool(row.get('is_default') or row.get('default')), 'full_street_redacted': True})
        return result

    def _slot_rows(self, address_id: str | int) -> list[Mapping[str, Any]]:
        encoded = quote(str(address_id), safe='')
        params = self._params()
        params['size'] = self._SLOTS_PAGE_SIZE
        payload, _ = self._request(
            'GET',
            f'/customers/{self._customer_id()}/addresses/{encoded}/slots/',
            params=params,
        )
        return self._collection_rows(payload)

    def slots(self, address_id: str | int) -> list[dict[str, Any]]:
        rows = self._slot_rows(address_id)
        if not isinstance(rows, list):
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            slot_id = str(row.get('id', '')).strip()
            if not slot_id:
                continue
            result.append({'id': slot_id, 'start': row.get('start') or row.get('start_date'), 'end': row.get('end') or row.get('end_date'), 'price': float(as_decimal(row.get('price'))), 'price_text': money(as_decimal(row.get('price'))), 'available': row.get('available') is True, 'open': row.get('open') is True, 'cutoff_time': row.get('cutoff_time'), 'timezone': row.get('timezone')})
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
        if total <= 0:
            raise InvalidRequest('Mercadona cart is empty or has no verifiable total')
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

    @classmethod
    def _checkout_cart_contract(
        cls, payload: Mapping[str, Any]
    ) -> tuple[str, int, list[dict[str, Any]], Decimal]:
        """Extract the cart snapshot returned by the checkout endpoint.

        Checkout creation is a write.  Its response is only an acknowledgement;
        the subsequent GET must expose enough cart state to compare it with the
        reviewed cart.  Do not silently accept a partial checkout response.
        """

        cart = payload.get('cart')
        if not isinstance(cart, Mapping):
            cart = {}
        cart_id = str(
            cart.get('id')
            or payload.get('cart_id')
            or payload.get('cart_uuid')
            or ''
        ).strip()
        raw_version = cart.get('version')
        if raw_version in (None, ''):
            raw_version = payload.get('cart_version')
        version = cls._cart_version({'version': raw_version})
        raw_lines = cart.get('lines')
        if not isinstance(raw_lines, list):
            raw_lines = payload.get('lines')
        if not isinstance(raw_lines, list):
            raise ProviderError(
                'Mercadona authoritative checkout omitted cart lines'
            )
        lines: list[dict[str, Any]] = []
        for raw_line in raw_lines:
            if not isinstance(raw_line, Mapping):
                raise ProviderError(
                    'Mercadona authoritative checkout contained malformed cart lines'
                )
            product = raw_line.get('product')
            product = product if isinstance(product, Mapping) else {}
            product_id = str(
                raw_line.get('product_id') or product.get('id') or ''
            ).strip()
            quantity = as_decimal(raw_line.get('quantity'))
            if product_id and quantity > 0:
                lines.append({'product_id': product_id, 'quantity': float(quantity)})
        total = cls._extract_total(cart)
        if total <= 0:
            total = cls._extract_total(payload)
        if not cart_id or total <= 0:
            raise ProviderError(
                'Mercadona authoritative checkout omitted cart identity or total'
            )
        return cart_id, version, lines, total

    @classmethod
    def _validate_authoritative_checkout(
        cls,
        payload: Mapping[str, Any],
        *,
        checkout_id: str,
        expected_cart: Mapping[str, Any],
        expected_cart_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = cls._normalize_checkout(payload)
        if normalized['checkout_id'] != str(checkout_id):
            raise ProviderError(
                'Mercadona authoritative checkout id differs from the creation response'
            )
        cart_id, version, lines, cart_total = cls._checkout_cart_contract(payload)
        expected_id = str(expected_cart_payload.get('id') or '').strip()
        expected_version = int(expected_cart.get('version') or -1)
        expected_lines = [
            line for line in expected_cart.get('lines', []) if isinstance(line, Mapping)
        ]
        expected_total = as_decimal(expected_cart.get('total'))
        if cart_id != expected_id:
            raise ConcurrentCartChange(
                'Mercadona checkout cart identity differs from the reviewed cart'
            )
        if version != expected_version:
            raise ConcurrentCartChange(
                'Mercadona checkout cart version differs from the reviewed cart'
            )
        if cls._line_signature(lines) != cls._line_signature(expected_lines):
            raise ConcurrentCartChange(
                'Mercadona checkout cart lines differ from the reviewed cart'
            )
        if cart_total != expected_total:
            raise ConcurrentCartChange(
                'Mercadona checkout cart total differs from the reviewed cart'
            )
        checkout_total = as_decimal(normalized.get('total'))
        if checkout_total != expected_total:
            raise ConcurrentCartChange(
                'Mercadona authoritative checkout total differs from the reviewed cart'
            )
        return normalized

    def create_checkout(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        max_total = as_decimal(plan.get('max_total'))
        expected = plan.get('cart', {})
        expected_payload = plan.get('cart_payload', {})
        if not isinstance(expected, Mapping) or not isinstance(
            expected_payload, Mapping
        ):
            raise InvalidRequest('Mercadona checkout plan is malformed')
        current_raw = self._cart_raw()
        current = self._normalize_cart(current_raw)
        if int(current['version']) != int(expected.get('version') or -1):
            raise ConcurrentCartChange('Mercadona cart changed after checkout review')
        if str(current_raw.get('id') or '') != str(expected_payload.get('id') or ''):
            raise ConcurrentCartChange('Mercadona cart identity changed after review')
        current_lines = [
            line
            for line in current.get('lines', [])
            if isinstance(line, Mapping)
        ]
        expected_lines = [
            line
            for line in expected.get('lines', [])
            if isinstance(line, Mapping)
        ]
        if self._line_signature(current_lines) != self._line_signature(expected_lines):
            raise ConcurrentCartChange('Mercadona cart lines changed after review')
        current_prices = {
            str(line.get('product_id') or ''): as_decimal(line.get('unit_price'))
            for line in current_lines
        }
        expected_prices = {
            str(line.get('product_id') or ''): as_decimal(line.get('unit_price'))
            for line in expected_lines
        }
        if current_prices != expected_prices:
            raise ConcurrentCartChange('Mercadona cart prices changed after review')
        current_total = as_decimal(current['total'])
        if current_total != as_decimal(expected.get('total')):
            raise ConcurrentCartChange('Mercadona cart total changed after review')
        if current_total > max_total:
            raise BudgetExceeded('Mercadona cart now exceeds the approved checkout cap')
        try:
            payload, _ = self._request(
                'POST',
                f'/customers/{self._customer_id()}/checkouts/',
                json_body={'cart': plan.get('cart_payload', {})},
                params=self._params(),
            )
        except AuthenticationRequired:
            raise
        except Exception as exc:
            status_code = getattr(exc, 'status_code', None)
            failure_kind = (
                f'HTTP {status_code}'
                if isinstance(status_code, int)
                else type(exc).__name__
            )
            raise ProviderError(
                f'Mercadona checkout creation result is ambiguous ({failure_kind}); '
                'the POST was not '
                'retried and the checkout must be inspected before any further write',
                status_code=status_code,
                operation='checkout_create',
            ) from exc
        if not isinstance(payload, Mapping):
            raise ProviderError('Mercadona checkout creation returned an invalid response')
        checkout_id = str(payload.get('id') or payload.get('checkout_id') or '').strip()
        if not checkout_id:
            raise ProviderError('Mercadona checkout creation returned no checkout id')
        try:
            authoritative_payload, _ = self._request(
                'GET',
                f'/customers/{self._customer_id()}/checkouts/{quote(checkout_id, safe="")}/',
                params=self._params(),
            )
        except Exception as exc:
            raise ProviderError(
                'Mercadona acknowledged checkout creation, but its authoritative '
                'reread failed; do not create another checkout automatically'
            ) from exc
        if not isinstance(authoritative_payload, Mapping):
            raise ProviderError('Mercadona authoritative checkout response was invalid')
        normalized = self._validate_authoritative_checkout(
            authoritative_payload,
            checkout_id=checkout_id,
            expected_cart=expected,
            expected_cart_payload=expected_payload,
        )
        if as_decimal(normalized['total']) <= 0:
            raise ProviderError('Mercadona checkout returned no positive authoritative total')
        if as_decimal(normalized['total']) > max_total:
            raise BudgetExceeded('Mercadona checkout total exceeds the approved cap')
        normalized['checkout_created'] = True
        return normalized

    def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        encoded = quote(checkout_id, safe='')
        payload, _ = self._request(
            'GET',
            f'/customers/{self._customer_id()}/checkouts/{encoded}/',
            params=self._params(),
        )
        if not isinstance(payload, Mapping):
            raise ProviderError('Mercadona checkout response was invalid')
        return self._normalize_checkout(payload)

    def set_checkout_delivery(self, checkout_id: str, *, address_id: str | int, slot_id: str, max_total: Decimal) -> dict[str, Any]:
        if max_total <= 0:
            raise InvalidRequest('max_total must be greater than zero')
        if str(address_id).strip() == '' or not str(slot_id).strip():
            raise InvalidRequest('Mercadona delivery needs address_id and slot_id')
        address = next(
            (
                row
                for row in self._address_rows()
                if str(row.get('id')) == str(address_id)
            ),
            None,
        )
        if address is None:
            raise InvalidRequest('selected Mercadona delivery address is not available')
        offered_rows = self._slot_rows(address_id)
        offered: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
        for raw_slot in offered_rows:
            slot_id_value = str(raw_slot.get('id', '')).strip()
            if not slot_id_value:
                continue
            price = as_decimal(raw_slot.get('price'))
            offered.append(
                (
                    {
                        'id': slot_id_value,
                        'start': raw_slot.get('start') or raw_slot.get('start_date'),
                        'end': raw_slot.get('end') or raw_slot.get('end_date'),
                        'price': float(price),
                        'price_text': money(price),
                        'available': raw_slot.get('available') is True,
                        'open': raw_slot.get('open') is True,
                    },
                    raw_slot,
                )
            )
        selected = next(
            (pair for pair in offered if str(pair[0].get('id')) == str(slot_id)),
            None,
        )
        if (
            selected is None
            or not selected[0].get('available')
            or not selected[0].get('open')
        ):
            raise InvalidRequest('selected Mercadona delivery slot is not available')
        selected, selected_row = selected
        before = self.get_checkout(checkout_id)
        before_total = as_decimal(before.get('total'))
        if before_total <= 0 or before_total > max_total:
            raise BudgetExceeded(
                'Mercadona checkout total is outside the approved cap before delivery'
            )
        if (
            before.get('address_id') in (None, '')
            and before.get('slot_id') in (None, '')
            and before_total + as_decimal(selected.get('price')) > max_total
        ):
            raise BudgetExceeded(
                'Mercadona checkout plus the selected delivery fee exceeds the cap'
            )
        encoded = quote(checkout_id, safe='')
        body = {
            'address': self._delivery_address_payload(address),
            'slot': self._delivery_slot_payload(selected_row),
        }
        try:
            self._request(
                'PUT',
                f'/customers/{self._customer_id()}/checkouts/{encoded}/delivery-info/',
                json_body=body,
                params=self._params(),
            )
            # The PUT response is only an acknowledgement.  Always reread the
            # checkout to verify the resulting total and selected objects.
            observed = self.get_checkout(checkout_id)
        except Exception as original:
            try:
                observed = self.get_checkout(checkout_id)
            except Exception as read_error:
                raise ProviderError(
                    'Mercadona delivery update result is ambiguous and checkout '
                    'could not be reread; do not retry automatically'
                ) from read_error
            if (
                str(observed.get('address_id')) != str(address_id)
                or str(observed.get('slot_id')) != str(slot_id)
            ):
                raise ProviderError(
                    'Mercadona delivery update failed; checkout did not reach the '
                    'reviewed address and slot'
                ) from original
        normalized = observed
        if not isinstance(normalized, Mapping):
            raise ProviderError('Mercadona checkout reread returned an invalid response')
        if normalized.get('checkout_id') != str(checkout_id):
            raise ProviderError('Mercadona checkout reread returned a different checkout')
        total = as_decimal(normalized['total'])
        if total <= 0:
            normalized = self.get_checkout(checkout_id)
            total = as_decimal(normalized['total'])
        if total <= 0:
            raise BudgetExceeded('could not verify checkout total after selecting delivery')
        if (
            str(normalized.get('address_id')) != str(address_id)
            or str(normalized.get('slot_id')) != str(slot_id)
        ):
            raise ProviderError(
                'Mercadona checkout did not preserve the reviewed address and slot'
            )
        if total > max_total:
            previous_address = before.get('address_id')
            previous_slot = before.get('slot_id')
            if previous_address not in (None, '') and previous_slot not in (None, ''):
                try:
                    previous_address_row = next(
                        (
                            row
                            for row in self._address_rows()
                            if str(row.get('id')) == str(previous_address)
                        ),
                        None,
                    )
                    previous_slot_row = next(
                        (
                            row
                            for row in self._slot_rows(previous_address)
                            if str(row.get('id')) == str(previous_slot)
                        ),
                        None,
                    )
                    if previous_address_row is None or previous_slot_row is None:
                        raise ProviderError('previous delivery objects could not be reread')
                    self._request(
                        'PUT',
                        f'/customers/{self._customer_id()}/checkouts/{encoded}/delivery-info/',
                        json_body={
                            'address': self._delivery_address_payload(
                                previous_address_row
                            ),
                            'slot': self._delivery_slot_payload(previous_slot_row),
                        },
                        params=self._params(),
                    )
                    restored = self.get_checkout(checkout_id)
                    if (
                        str(restored.get('address_id')) != str(previous_address)
                        or str(restored.get('slot_id')) != str(previous_slot)
                    ):
                        raise ProviderError('delivery rollback did not restore checkout')
                except Exception as rollback_error:
                    raise BudgetExceeded(
                        'checkout exceeded the cap and delivery rollback could not '
                        'be verified'
                    ) from rollback_error
            else:
                raise ProviderError(
                    'checkout exceeded the cap after delivery selection, but the '
                    'initial checkout had no complete delivery selection and '
                    'Mercadona exposes no safe delivery-clear operation; inspect '
                    'the checkout and do not retry automatically'
                )
            raise BudgetExceeded(f'checkout total {money(total)} EUR exceeds cap {money(max_total)} EUR')
        normalized['delivery_updated'] = True
        return normalized

    def submit_order(self, checkout_id: str, *, max_total: Decimal) -> dict[str, Any]:
        if os.getenv('OPEN_GROCERY_ENABLE_RETAILER_WRITES', '').casefold() not in {'1', 'true', 'yes', 'on'}:
            raise OrderSubmissionDisabled('retailer writes are disabled')
        enabled = os.getenv('OPEN_GROCERY_ENABLE_ORDER_SUBMISSION', '').casefold() in {'1', 'true', 'yes', 'on'}
        if not enabled:
            raise OrderSubmissionDisabled("order submission is disabled; set OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1 only on the user's own local machine")
        checkout = self.get_checkout(checkout_id)
        if checkout.get('address_id') in (None, '') or checkout.get('slot_id') in (None, ''):
            raise InvalidRequest(
                'Mercadona checkout needs address and slot before order submission'
            )
        total = as_decimal(checkout.get('total'))
        if total <= 0:
            raise BudgetExceeded('could not verify an authoritative checkout total; refusing')
        if total > max_total:
            raise BudgetExceeded(f'checkout total {money(total)} EUR exceeds cap {money(max_total)} EUR')
        self._mark_order_attempt(checkout_id)
        encoded = quote(checkout_id, safe='')
        try:
            payload, _ = self._request(
                'POST',
                f'/customers/{self._customer_id()}/checkouts/{encoded}/confirm/',
                params=self._params(),
            )
        except Exception as exc:
            raise ProviderError(
                'Mercadona order submission was attempted but its result is '
                'unverified; inspect order history and do not retry'
            ) from exc
        result = dict(payload) if isinstance(payload, Mapping) else {'response': payload}
        order_id = result.get('order_id') or result.get('id')
        if not order_id:
            raise ProviderError(
                'Mercadona order submission returned no verifiable order id; '
                'inspect order history and do not retry'
            )
        result.update({'store': 'mercadona', 'checkout_id': checkout_id, 'authorized_total': float(total), 'authorized_total_text': money(total), 'order_placed': True, 'order_id': order_id})
        return result
