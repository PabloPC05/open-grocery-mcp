"""Mercadona real-cart reads and update planning."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from open_grocery_mcp.errors import BudgetExceeded, ConcurrentCartChange, InvalidRequest, ProviderError
from open_grocery_mcp.models import as_decimal, money
from open_grocery_mcp.providers.browser_normalize import is_restricted_product

_MAX_CART_LINES = 100
_MAX_QUANTITY = Decimal('1000')
_PROGRAMMATIC_SOURCE_CODE = 'CA'


class MercadonaCartMixin:

    @staticmethod
    def _cart_version(cart: Mapping[str, Any]) -> int:
        """Return the retailer cart version, rejecting an ambiguous payload."""

        raw = cart.get('version')
        if isinstance(raw, bool) or raw in (None, ''):
            raise ProviderError('Mercadona cart response did not expose a version')
        try:
            version = int(raw)
        except (TypeError, ValueError, OverflowError):
            raise ProviderError('Mercadona cart response contained an invalid version') from None
        if version < 0:
            raise ProviderError('Mercadona cart response contained an invalid version')
        if str(raw).strip() not in {str(version), f'{version}.0'}:
            raise ProviderError('Mercadona cart response contained an invalid version')
        return version

    @staticmethod
    def _validate_line_metadata(line: Mapping[str, Any]) -> None:
        """Reject line metadata the public bundle cannot serialize safely."""

        for key in ('id', 'version'):
            if key not in line or line[key] in (None, ''):
                continue
            value = line[key]
            if isinstance(value, (Mapping, list, tuple, set)) or not isinstance(
                value, (str, int, float, bool)
            ):
                raise ProviderError(
                    f'Mercadona cart line {key} must be a primitive value'
                )
            if key == 'id' and (isinstance(value, bool) or not str(value).strip()):
                raise ProviderError('Mercadona cart line id is invalid')
            if key == 'version':
                if isinstance(value, bool):
                    raise ProviderError('Mercadona cart line version is invalid')
                try:
                    parsed = int(value)
                except (TypeError, ValueError, OverflowError):
                    raise ProviderError(
                        'Mercadona cart line version is invalid'
                    ) from None
                if parsed < 0 or str(value).strip() not in {
                    str(parsed),
                    f'{parsed}.0',
                }:
                    raise ProviderError('Mercadona cart line version is invalid')

        if 'sources' not in line:
            return
        sources = line['sources']
        if not isinstance(sources, list) or any(
            not isinstance(source, str) or not source.strip() for source in sources
        ):
            raise ProviderError(
                'Mercadona cart line sources must be a list of primitive strings'
            )

    @classmethod
    def _validate_source_transition(
        cls,
        previous: Mapping[str, Any],
        desired_quantity: Decimal,
        desired_sources: Sequence[str],
        *,
        product_id: str,
    ) -> None:
        """Ensure source operation history agrees with a quantity change.

        The storefront reducer appends ``+source``/``-source`` operations; it
        does not attach quantities to source objects.  If an existing line has
        source history, a quantity change without a corresponding appended
        operation cannot be serialized safely.
        """

        previous_quantity = as_decimal(previous.get('quantity'))
        previous_sources = previous.get('sources', [])
        previous_sources = list(previous_sources) if isinstance(previous_sources, list) else []
        if desired_quantity == previous_quantity:
            return
        if previous_sources and list(desired_sources[: len(previous_sources)]) != previous_sources:
            raise InvalidRequest(
                f'Mercadona quantity change for {product_id!r} must preserve '
                'existing source history'
            )
        delta = list(desired_sources[len(previous_sources) :])
        prefix = '+' if desired_quantity > previous_quantity else '-'
        expected_operations = cls._source_operation_count(
            previous, desired_quantity - previous_quantity
        )
        if (
            len(delta) != expected_operations
            or any(not source.startswith(prefix) for source in delta)
        ):
            raise InvalidRequest(
                f'Mercadona quantity change for {product_id!r} needs explicit '
                f"'{prefix}source' operations for each quantity increment"
            )

    @staticmethod
    def _source_operation_count(
        previous: Mapping[str, Any], quantity_delta: Decimal
    ) -> int:
        """Return the number of reducer operations represented by a delta.

        Mercadona's public reducer stores one source token per quantity
        increment.  Bulk products expose that increment as
        ``price_instructions.increment_bunch_amount``; ordinary products use
        a one-unit fallback.
        """

        amount = abs(quantity_delta)
        if amount <= 0:
            return 0
        product = previous.get('product')
        pricing = product.get('price_instructions') if isinstance(product, Mapping) else None
        raw_increment = pricing.get('increment_bunch_amount') if isinstance(pricing, Mapping) else None
        try:
            increment = Decimal(str(raw_increment)) if raw_increment not in (None, '') else Decimal('1')
        except (InvalidOperation, ValueError, TypeError):
            increment = Decimal('1')
        if not increment.is_finite() or increment <= 0:
            increment = Decimal('1')
        return max(1, int((amount / increment).to_integral_value(rounding=ROUND_CEILING)))

    @staticmethod
    def _validated_quantity(value: Any, *, product_id: str) -> Decimal:
        if isinstance(value, bool):
            raise InvalidRequest(f'invalid quantity for Mercadona product {product_id!r}')
        try:
            quantity = Decimal(str(value).replace(',', '.').strip())
        except (InvalidOperation, ValueError, AttributeError):
            raise InvalidRequest(
                f'invalid quantity for Mercadona product {product_id!r}'
            ) from None
        if not quantity.is_finite() or quantity < 0:
            raise InvalidRequest(f'invalid quantity for Mercadona product {product_id!r}')
        if quantity > _MAX_QUANTITY:
            raise InvalidRequest(
                f'quantity for Mercadona product {product_id!r} exceeds the '
                f'safety limit of {_MAX_QUANTITY}'
            )
        return quantity

    def _cart_raw(self) -> dict[str, Any]:
        payload, _ = self._request('GET', f'/customers/{self._customer_id()}/cart/', params=self._params())
        if not isinstance(payload, dict):
            raise ProviderError('Mercadona cart response was not an object')
        cart_id = payload.get('id')
        if isinstance(cart_id, (Mapping, list, tuple, set)) or not isinstance(
            cart_id, (str, int)
        ) or isinstance(cart_id, bool) or not str(cart_id).strip():
            raise ProviderError('Mercadona cart response did not expose an id')
        self._cart_version(payload)
        if not isinstance(payload.get('lines'), list):
            raise ProviderError('Mercadona cart response did not expose lines')
        for line in payload['lines']:
            if not isinstance(line, Mapping):
                raise ProviderError('Mercadona cart response contained an invalid line')
            self._validate_line_metadata(line)
            product = line.get('product') if isinstance(line.get('product'), Mapping) else {}
            product_id = str(line.get('product_id') or product.get('id') or '').strip()
            if not product_id:
                raise ProviderError(
                    'Mercadona cart response contained a line without a product id'
                )
            try:
                quantity = self._validated_quantity(
                    line.get('quantity'), product_id=product_id
                )
            except InvalidRequest as exc:
                raise ProviderError(
                    'Mercadona cart response contained an invalid line quantity'
                ) from exc
            if quantity <= 0:
                raise ProviderError(
                    'Mercadona cart response contained a non-positive line quantity'
                )
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
            serialized: dict[str, Any] = {
                'product_id': product_id,
                'quantity': float(quantity),
                'sources': list(sources) if isinstance(sources, list) else [],
            }
            # Mercadona's PUT contract accepts line identity/version when the
            # line came from the cart response.  Keep those fields for an
            # update/rollback, while omitting them for synthetic lines.
            for key in ('id', 'version'):
                if key in line and line[key] not in (None, ''):
                    serialized[key] = line[key]
            result.append(serialized)
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
            entry: dict[str, Any] = {'product_id': product_id, 'name': str(product.get('display_name') or line.get('display_name') or ''), 'quantity': float(quantity), 'unit_price': float(unit_price), 'unit_price_text': money(unit_price), 'line_total': float(unit_price * quantity), 'line_total_text': money(unit_price * quantity), 'sources': line.get('sources') if isinstance(line.get('sources'), list) else []}
            for key in ('id', 'version'):
                if key in line and line[key] not in (None, ''):
                    entry[key] = line[key]
            normalized.append(entry)
        return {'store': 'mercadona', 'cart_id': str(cart.get('id', '')), 'version': cls._cart_version(cart), 'products_count': int(cart.get('products_count') or len(normalized)), 'total': float(total), 'total_text': money(total), 'currency': 'EUR', 'lines': normalized}

    def cart(self) -> dict[str, Any]:
        return self._normalize_cart(self._cart_raw())

    def _product_unit_price(self, product_id: str) -> Decimal:
        payload, _ = self._request('GET', f"/products/{quote(product_id, safe='')}/", params=self._params())
        if not isinstance(payload, Mapping):
            raise ProviderError(f'Mercadona product {product_id!r} returned no detail')
        returned_id = str(payload.get('id') or '').strip()
        if returned_id and returned_id != product_id:
            raise ProviderError(
                f'Mercadona product detail did not match requested id {product_id!r}'
            )
        if payload.get('published') is False or payload.get('available') is False:
            raise ProviderError(f'Mercadona product {product_id!r} is unavailable')
        category = payload.get('category')
        category_name = (
            str(category.get('name') or '')
            if isinstance(category, Mapping)
            else str(category or '')
        )
        name = str(payload.get('display_name') or payload.get('name') or '')
        if is_restricted_product(name, category_name):
            raise InvalidRequest(
                f'automated purchase of age-restricted product {name or product_id!r} '
                'is not supported'
            )
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
        if len(changes) > _MAX_CART_LINES:
            raise InvalidRequest(
                f'Mercadona cart updates are limited to {_MAX_CART_LINES} product lines'
            )
        cart = self._cart_raw()
        version = int(cart.get('version') or 0)
        if expected_version is not None and version != expected_version:
            raise ConcurrentCartChange(f'Mercadona cart version is {version}, not reviewed version {expected_version}')
        previous_lines = self._write_lines(cart)
        current = {line['product_id']: dict(line) for line in previous_lines}
        raw_current: dict[str, Mapping[str, Any]] = {}
        for raw_line in self._raw_lines(cart):
            product = raw_line.get('product') if isinstance(raw_line.get('product'), Mapping) else {}
            raw_product_id = str(raw_line.get('product_id') or product.get('id') or '').strip()
            if raw_product_id:
                raw_current[raw_product_id] = raw_line
        desired: dict[str, dict[str, Any]] = {} if mode == 'replace' else current
        for change in changes:
            product_id = str(change.get('product_id', '')).strip()
            if not product_id:
                raise InvalidRequest('every cart change needs product_id')
            if 'quantity' not in change:
                raise InvalidRequest(
                    f'every Mercadona cart change needs quantity for {product_id!r}'
                )
            quantity = self._validated_quantity(
                change.get('quantity'), product_id=product_id
            )
            if is_restricted_product(
                str(change.get('name') or ''),
                str(change.get('category') or ''),
            ):
                raise InvalidRequest(
                    f'automated purchase of age-restricted product '
                    f'{change.get("name") or product_id!r} is not supported'
                )
            if quantity <= 0:
                desired.pop(product_id, None)
                continue
            # For an existing line, retain retailer metadata unless the
            # caller explicitly supplies a replacement.  New lines carry only
            # the fields accepted for a synthetic line.
            line = dict(current.get(product_id, {'product_id': product_id}))
            line['product_id'] = product_id
            line['quantity'] = float(quantity)
            for key in ('id', 'version'):
                if key in change and change[key] not in (None, ''):
                    line[key] = change[key]
            if 'sources' in change:
                sources = change.get('sources')
                if not isinstance(sources, list):
                    raise InvalidRequest(
                        f'Mercadona cart line sources for {product_id!r} must be a list'
                    )
                line['sources'] = list(sources)
            else:
                previous = current.get(product_id)
                previous_sources = (
                    list(previous.get('sources', []))
                    if isinstance(previous, Mapping)
                    and isinstance(previous.get('sources', []), list)
                    else []
                )
                if previous is None:
                    operation_count = self._source_operation_count({}, quantity)
                    previous_sources.extend(
                        f'+{_PROGRAMMATIC_SOURCE_CODE}' for _ in range(operation_count)
                    )
                else:
                    previous_quantity = as_decimal(previous.get('quantity'))
                    validation_previous = raw_current.get(product_id, previous)
                    if quantity > previous_quantity:
                        operation_count = self._source_operation_count(
                            validation_previous, quantity - previous_quantity
                        )
                        previous_sources.extend(
                            f'+{_PROGRAMMATIC_SOURCE_CODE}'
                            for _ in range(operation_count)
                        )
                    elif quantity < previous_quantity:
                        operation_count = self._source_operation_count(
                            validation_previous, quantity - previous_quantity
                        )
                        previous_sources.extend(
                            f'-{_PROGRAMMATIC_SOURCE_CODE}'
                            for _ in range(operation_count)
                        )
                line['sources'] = previous_sources
            try:
                self._validate_line_metadata(line)
            except ProviderError as exc:
                if any(key in change for key in ('id', 'version', 'sources')):
                    raise InvalidRequest(str(exc)) from exc
                raise
            self._validate_source_transition(
                raw_current.get(product_id, current.get(product_id, {})),
                quantity,
                line['sources'],
                product_id=product_id,
            )
            desired[product_id] = line
        desired_ids = set(desired)
        retained_restricted = next(
            (
                line
                for line in self._normalize_cart(cart).get('lines', [])
                if isinstance(line, Mapping)
                and str(line.get('product_id') or '') in desired_ids
                and is_restricted_product(line.get('name'))
            ),
            None,
        )
        if retained_restricted is not None:
            raise InvalidRequest(
                'automated Mercadona cart changes cannot retain age-restricted products'
            )
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
        previous_cart = self._normalize_cart(cart)
        return {'store': 'mercadona', 'cart_id': str(cart.get('id', '')), 'expected_cart_version': version, 'mode': mode, 'max_total': float(max_total), 'max_total_text': money(max_total), 'estimated_total': float(total), 'estimated_total_text': money(total), 'currency': 'EUR', 'lines': public_lines, 'desired_lines': list(desired.values()), 'previous_lines': previous_lines, 'reviewed_unit_prices': {line['product_id']: line['unit_price'] for line in public_lines}, 'previous_unit_prices': {str(line.get('product_id') or ''): float(as_decimal(line.get('unit_price'))) for line in previous_cart.get('lines', []) if isinstance(line, Mapping) and line.get('product_id')}, 'previous_total': float(as_decimal(previous_cart.get('total'))), 'retailer_cart_modified': False}
