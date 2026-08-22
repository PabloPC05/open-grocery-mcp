"""Two-phase delivery, checkout and order workflows."""

from __future__ import annotations

from typing import Any

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.models import as_decimal, money
from open_grocery_mcp.workflow_base import _require_order_approval, _require_retailer_writes


class CheckoutWorkflowMixin:
    """Two-phase delivery, checkout and order workflows."""

    def delivery_addresses(self, store: str) -> list[dict[str, Any]]:
        return self._delivery_provider(store).delivery_addresses()

    def delivery_slots(self, store: str, address_id: str | int) -> list[dict[str, Any]]:
        return self._delivery_provider(store).delivery_slots(address_id)

    def prepare_checkout_creation(
        self,
        *,
        store: str,
        max_total: float,
        expected_cart_version: int | None,
        shipping_address_id: str | int | None = None,
        delivery_date: str | None = None,
        schedule_range_id: str | int | None = None,
    ) -> dict[str, Any]:
        plan = self._checkout_provider(store).preview_checkout(expected_version=expected_cart_version, max_total=as_decimal(max_total))
        delivery = {
            'shipping_address_id': shipping_address_id,
            'delivery_date': (delivery_date or '').strip() or None,
            'schedule_range_id': schedule_range_id,
        }
        if any(value not in (None, '') for value in delivery.values()):
            if any(value in (None, '') for value in delivery.values()):
                raise InvalidRequest('checkout creation needs the full delivery triple: address id, delivery date and schedule range')
            plan['delivery'] = delivery
        total_text = plan['cart']['total_text']
        return self.confirmations.create(action='checkout_create', phrase=f'CREAR CHECKOUT {total_text} EUR', payload={'store': store, 'plan': plan}, summary=self._public_plan(plan))

    def commit_checkout_creation(self, confirmation_id: str, confirmation_phrase: str) -> dict[str, Any]:
        _require_retailer_writes()
        payload = self.confirmations.consume(confirmation_id, confirmation_phrase, action='checkout_create')
        return self._checkout_provider(str(payload['store'])).create_checkout(payload['plan'])

    def get_checkout(self, store: str, checkout_id: str) -> dict[str, Any]:
        return self._checkout_provider(store).get_checkout(checkout_id)

    def prepare_delivery_selection(self, *, store: str, checkout_id: str, address_id: str | int, slot_id: str, max_total: float) -> dict[str, Any]:
        slots = self._delivery_provider(store).delivery_slots(address_id)
        selected = next((slot for slot in slots if str(slot.get('id')) == str(slot_id)), None)
        if selected is None or not selected.get('available'):
            raise InvalidRequest('selected delivery slot is not currently available')
        checkout = self._checkout_provider(store).get_checkout(checkout_id)
        summary = {'store': store, 'checkout': checkout, 'address_id': address_id, 'slot': selected, 'max_total': max_total, 'state_changed': False}
        return self.confirmations.create(action='delivery_select', phrase=f'CONFIRMAR ENTREGA {slot_id}', payload={'store': store, 'checkout_id': checkout_id, 'address_id': address_id, 'slot_id': slot_id, 'max_total': max_total}, summary=summary)

    def commit_delivery_selection(self, confirmation_id: str, confirmation_phrase: str) -> dict[str, Any]:
        _require_retailer_writes()
        payload = self.confirmations.consume(confirmation_id, confirmation_phrase, action='delivery_select')
        return self._checkout_provider(str(payload['store'])).set_checkout_delivery(str(payload['checkout_id']), address_id=payload['address_id'], slot_id=str(payload['slot_id']), max_total=as_decimal(payload['max_total']))

    def _validated_checkout_delivery(self, store: str, checkout_id: str) -> dict[str, Any]:
        checkout = self._checkout_provider(store).get_checkout(checkout_id)
        address_id = checkout.get('address_id')
        slot_id = checkout.get('slot_id')
        if address_id in (None, '') or slot_id in (None, ''):
            raise InvalidRequest('checkout needs both a saved delivery address and a delivery slot before purchase')
        slots = self._delivery_provider(store).delivery_slots(address_id)
        selected = next((slot for slot in slots if str(slot.get('id')) == str(slot_id)), None)
        if selected is None or not selected.get('available') or (not selected.get('open', True)):
            raise InvalidRequest('the checkout delivery slot is no longer available')
        return checkout

    def prepare_order_submission(self, *, store: str, checkout_id: str, max_total: float) -> dict[str, Any]:
        checkout = self._validated_checkout_delivery(store, checkout_id)
        total = as_decimal(checkout.get('total'))
        cap = as_decimal(max_total)
        if total <= 0:
            raise InvalidRequest('checkout has no verifiable positive total')
        if total > cap:
            raise InvalidRequest(f'checkout total {money(total)} EUR exceeds cap {money(cap)} EUR')
        return self.confirmations.create(action='order_submit', phrase=f'COMPRAR {money(total)} EUR', payload={'store': store, 'checkout_id': checkout_id, 'max_total': float(cap)}, summary={'store': store, 'checkout': checkout, 'authorized_total_text': money(total), 'order_placed': False, 'local_approval_code_required': True})

    def submit_order(self, confirmation_id: str, confirmation_phrase: str, approval_code: str) -> dict[str, Any]:
        _require_retailer_writes()
        _require_order_approval(approval_code)
        payload = self.confirmations.consume(confirmation_id, confirmation_phrase, action='order_submit')
        store = str(payload['store'])
        checkout_id = str(payload['checkout_id'])
        self._validated_checkout_delivery(store, checkout_id)
        return self._checkout_provider(store).submit_order(checkout_id, max_total=as_decimal(payload['max_total']))
