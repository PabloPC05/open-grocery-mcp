"""Verified Mercadona cart commits and rollback."""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from open_grocery_mcp.errors import (
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    ProviderError,
)
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

    @staticmethod
    def _source_signature(line: Mapping[str, Any]) -> tuple[str, ...] | None:
        sources = line.get('sources', [])
        if not isinstance(sources, list) or not sources:
            return None
        return tuple(
            source[:1]
            if isinstance(source, str) and source[:1] in {'+', '-'}
            else '<opaque>'
            for source in sources
        )

    @classmethod
    def _source_history_matches(
        cls,
        actual_lines: Sequence[Mapping[str, Any]],
        expected_lines: Sequence[Mapping[str, Any]],
    ) -> bool:
        """Check stable source-operation signs when the retailer returns them.

        Some cart responses omit ``sources`` after a write; absence is not
        evidence of a different operation history.  If both sides expose
        history, however, operation direction/order must match even when
        opaque retailer labels differ.
        """
        actual_by_id = {
            str(line.get('product_id') or '').strip(): line
            for line in actual_lines
            if str(line.get('product_id') or '').strip()
        }
        for expected in expected_lines:
            product_id = str(expected.get('product_id') or '').strip()
            expected_source = cls._source_signature(expected)
            actual = actual_by_id.get(product_id)
            if expected_source is None or actual is None:
                continue
            actual_source = cls._source_signature(actual)
            if actual_source is not None and actual_source != expected_source:
                return False
        return True

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

    def _validated_cart_result(
        self,
        raw: Mapping[str, Any],
        expected_lines: Sequence[Mapping[str, Any]],
        *,
        expected_cart_id: str,
        max_total: Any,
        expected_total: Any | None = None,
        expected_prices: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        actual_cart_id = str(raw.get('id') or '')
        if expected_cart_id and actual_cart_id != expected_cart_id:
            raise ProviderError('Mercadona returned a different cart after writing')
        if self._line_signature(self._write_lines(raw)) != self._line_signature(
            expected_lines
        ):
            raise ProviderError(
                'Mercadona cart did not match the reviewed product quantities'
            )
        if not self._source_history_matches(
            self._write_lines(raw), expected_lines
        ):
            raise ProviderError(
                'Mercadona cart did not preserve the reviewed source operations'
            )
        normalized = self._normalize_cart(raw)
        total = as_decimal(normalized.get('total'))
        if expected_lines and total <= 0:
            raise BudgetExceeded(
                'could not verify a positive Mercadona cart total after writing'
            )
        cap = as_decimal(max_total) if max_total is not None else None
        if cap is not None and total > cap:
            raise BudgetExceeded(
                f'Mercadona returned cart total {money(total)} EUR above cap '
                f'{money(cap)} EUR'
            )
        reviewed_total = (
            as_decimal(expected_total) if expected_total is not None else None
        )
        if reviewed_total is not None and total.quantize(
            as_decimal('0.01')
        ) != reviewed_total.quantize(as_decimal('0.01')):
            raise ProviderError(
                'Mercadona actual cart total differs from the reviewed total'
            )
        if expected_prices is not None:
            actual_prices = {
                str(line.get('product_id') or ''): as_decimal(line.get('unit_price'))
                for line in normalized.get('lines', [])
                if isinstance(line, Mapping) and line.get('product_id')
            }
            reviewed_prices = {
                str(product_id): as_decimal(price)
                for product_id, price in expected_prices.items()
            }
            if actual_prices != reviewed_prices:
                raise ProviderError(
                    'Mercadona cart prices did not match the reviewed prices'
                )
        return normalized

    def _restore_cart(
        self,
        *,
        cart_id: str,
        cart_version: int,
        previous_lines: Sequence[Mapping[str, Any]],
        previous_total: Any,
        previous_prices: Mapping[str, Any],
    ) -> None:
        self._request(
            'PUT',
            f'/customers/{self._customer_id()}/cart/',
            json_body={
                'id': cart_id,
                'version': cart_version,
                'lines': list(previous_lines),
            },
            params=self._params(),
        )
        restored = self._wait_for_cart_lines(previous_lines)
        self._validated_cart_result(
            restored,
            previous_lines,
            expected_cart_id=cart_id,
            max_total=None,
            expected_total=previous_total,
            expected_prices=previous_prices,
        )

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        desired_value = plan.get('desired_lines', [])
        previous_value = plan.get('previous_lines', [])
        if not isinstance(desired_value, list) or not isinstance(previous_value, list):
            raise InvalidRequest('Mercadona cart plan contains invalid line collections')
        desired = [dict(line) for line in desired_value if isinstance(line, Mapping)]
        previous = [dict(line) for line in previous_value if isinstance(line, Mapping)]
        if len(desired) != len(desired_value) or len(previous) != len(previous_value):
            raise InvalidRequest('Mercadona cart plan contains malformed lines')
        if len(desired) > 100:
            raise InvalidRequest('Mercadona cart plan exceeds the 100-line safety limit')
        max_total = as_decimal(plan.get('max_total'))
        expected_total = as_decimal(plan.get('estimated_total'))
        previous_total = as_decimal(plan.get('previous_total'))
        reviewed_prices = plan.get('reviewed_unit_prices', {})
        previous_prices = plan.get('previous_unit_prices', {})
        if not isinstance(reviewed_prices, Mapping) or not isinstance(
            previous_prices, Mapping
        ):
            raise InvalidRequest('Mercadona cart plan contains invalid price maps')
        if max_total <= 0:
            raise InvalidRequest('Mercadona cart plan has no positive total cap')
        if expected_total < 0 or previous_total < 0:
            raise InvalidRequest('Mercadona cart plan contains invalid totals')
        for label, lines in (('desired', desired), ('previous', previous)):
            seen: set[str] = set()
            for line in lines:
                try:
                    self._validate_line_metadata(line)
                except ProviderError as exc:
                    raise InvalidRequest(
                        f'Mercadona cart plan contains invalid {label} line metadata'
                    ) from exc
                product_id = str(line.get('product_id') or '').strip()
                if (
                    not product_id
                    or self._validated_quantity(
                        line.get('quantity'), product_id=product_id
                    )
                    <= 0
                    or product_id in seen
                ):
                    raise InvalidRequest(
                        f'Mercadona cart plan contains an invalid {label} line'
                    )
                seen.add(product_id)
        calculated_total = sum(
            (
                as_decimal(reviewed_prices.get(str(line.get('product_id') or '')))
                * as_decimal(line.get('quantity'))
                for line in desired
            ),
            as_decimal('0'),
        )
        if any(
            as_decimal(reviewed_prices.get(str(line.get('product_id') or ''))) <= 0
            for line in desired
        ) or calculated_total.quantize(as_decimal('0.01')) != expected_total.quantize(
            as_decimal('0.01')
        ):
            raise InvalidRequest(
                'Mercadona cart plan total no longer matches its reviewed prices'
            )
        expected_version = int(plan.get('expected_cart_version') or 0)
        current = self._cart_raw()
        current_version = int(current.get('version') or 0)
        if current_version != expected_version:
            raise ConcurrentCartChange(f'Mercadona cart changed from version {expected_version} to {current_version}; review again')
        cart_id = str(current.get('id') or plan.get('cart_id') or '')
        if not cart_id:
            raise ProviderError('Mercadona cart did not expose a usable id')
        reviewed_cart_id = str(plan.get('cart_id') or '')
        if reviewed_cart_id and reviewed_cart_id != cart_id:
            raise ConcurrentCartChange('Mercadona cart identity changed after review')
        # Cart version is part of Mercadona's optimistic-lock contract.  Do
        # not omit it even though the cart id alone is enough to route PUT.
        body = {'id': cart_id, 'version': expected_version, 'lines': desired}
        original: Exception | None = None
        try:
            self._request(
                'PUT',
                f'/customers/{self._customer_id()}/cart/',
                json_body=body,
                params=self._params(),
            )
            updated = self._wait_for_cart_lines(desired)
            normalized = self._validated_cart_result(
                updated,
                desired,
                expected_cart_id=cart_id,
                max_total=max_total,
                expected_total=expected_total,
                expected_prices=reviewed_prices,
            )
        except Exception as exc:
            original = exc

        if original is not None:
            try:
                observed = self._cart_raw()
            except Exception as read_error:
                raise ProviderError(
                    'Mercadona cart write result is ambiguous and the cart could not '
                    'be reread; inspect it before any further write'
                ) from read_error

            if self._line_signature(self._write_lines(observed)) == self._line_signature(
                desired
            ):
                try:
                    normalized = self._validated_cart_result(
                        observed,
                        desired,
                        expected_cart_id=cart_id,
                        max_total=max_total,
                        expected_total=expected_total,
                        expected_prices=reviewed_prices,
                    )
                except Exception:
                    pass
                else:
                    return {
                        **normalized,
                        'retailer_cart_modified': True,
                        'order_placed': False,
                        'write_response_ambiguous_but_state_verified': True,
                    }
            elif self._line_signature(
                self._write_lines(observed)
            ) == self._line_signature(previous):
                try:
                    self._validated_cart_result(
                        observed,
                        previous,
                        expected_cart_id=cart_id,
                        max_total=None,
                        expected_total=previous_total,
                        expected_prices=previous_prices,
                    )
                except Exception:
                    pass
                else:
                    raise ProviderError(
                        f'Mercadona cart update failed ({type(original).__name__}); '
                        'the previous cart remained unchanged'
                    ) from original
            else:
                raise ProviderError(
                    'Mercadona cart write failed and the observed cart differs from '
                    'both the desired and previous states; inspect it before any '
                    'further write'
                ) from original

            try:
                rollback_guard = self._cart_raw()
                if int(rollback_guard.get('version') or 0) != int(
                    observed.get('version') or 0
                ) or str(rollback_guard.get('id') or '') != str(
                    observed.get('id') or ''
                ):
                    raise ConcurrentCartChange(
                        'Mercadona cart changed again before rollback'
                    )
                self._restore_cart(
                    cart_id=cart_id,
                    cart_version=int(observed.get('version') or 0),
                    previous_lines=previous,
                    previous_total=previous_total,
                    previous_prices=previous_prices,
                )
            except Exception as rollback_error:
                raise ProviderError(
                    'Mercadona cart update failed and rollback could not be verified; '
                    'inspect the retailer cart before any further write'
                ) from rollback_error
            if isinstance(original, BudgetExceeded):
                raise BudgetExceeded(f'{original}; previous cart restored') from original
            raise ProviderError(
                f'Mercadona cart update failed ({type(original).__name__}); '
                'previous cart restored'
            ) from original
        normalized['retailer_cart_modified'] = True
        normalized['order_placed'] = False
        return normalized
