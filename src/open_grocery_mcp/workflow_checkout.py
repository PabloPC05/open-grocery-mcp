"""Two-phase delivery, checkout and order workflows."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.models import as_decimal, money
from open_grocery_mcp.workflow_base import (
    _require_order_approval,
    _require_order_submission_enabled,
    _require_retailer_writes,
)


class CheckoutWorkflowMixin:
    """Two-phase delivery, checkout and order workflows."""

    def _projected_delivery_total(
        self,
        *,
        store: str,
        checkout: dict[str, Any],
        address_id: str | int,
        selected: dict[str, Any],
    ) -> Decimal:
        total = as_decimal(checkout.get('total'))
        selected_price = as_decimal(selected.get('price'))
        current_address = checkout.get('address_id')
        current_slot = checkout.get('slot_id')
        if current_slot in (None, ''):
            return total + selected_price
        if (
            str(current_address) == str(address_id)
            and str(current_slot) == str(selected.get('id'))
        ):
            return total
        try:
            current_slots = self._delivery_provider(store).delivery_slots(
                current_address
            )
        except Exception:
            return total + selected_price
        previous = next(
            (
                slot
                for slot in current_slots
                if str(slot.get('id')) == str(current_slot)
            ),
            None,
        )
        if previous is None:
            return total + selected_price
        return max(total - as_decimal(previous.get('price')), as_decimal(0)) + selected_price

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
        return self._public_plan(
            self._checkout_provider(store).get_checkout(checkout_id)
        )

    def prepare_human_handoff(
        self,
        *,
        store: str,
        max_total: float,
        checkout_id: str | None = None,
        address_id: str | int | None = None,
        slot_id: str | None = None,
    ) -> dict[str, Any]:
        """Re-read the safest supported boundary before handing control to a human.

        Mercadona and Gadis must reach an authoritative checkout with a live
        address/slot.  Froiz and Eroski stop at a re-read cart because their
        first observed checkout write can already place the order.
        """

        cap = as_decimal(max_total)
        if not cap.is_finite() or cap <= 0:
            raise InvalidRequest("max_total must be a positive finite number")
        provider = self.registry.get(store)
        has_checkout = "checkout" in provider.info.capabilities

        if has_checkout:
            normalized_checkout_id = str(checkout_id or "").strip()
            if not normalized_checkout_id:
                raise InvalidRequest(
                    f"{provider.info.label} needs a checkout_id for final review"
                )
            checkout = self._validated_checkout_delivery(
                store,
                normalized_checkout_id,
            )
            total = as_decimal(checkout.get("total"))
            if not total.is_finite() or total <= 0 or total > cap:
                raise InvalidRequest(
                    f"checkout total must be positive and no greater than {money(cap)} EUR"
                )
            return self._public_plan(
                {
                    "store": store,
                    "handoff_stage": "checkout_review",
                    "ready_for_human_review": True,
                    "checkout": checkout,
                    "verified_total_text": money(total),
                    "max_total_text": money(cap),
                    "cart_verified": True,
                    "delivery_verified": True,
                    "checkout_verified": True,
                    "automated_order_submission": False,
                    "human_final_action_required": True,
                    "private_review_url_exposed": False,
                }
            )

        if checkout_id not in (None, ""):
            raise InvalidRequest(
                f"{provider.info.label} has no separate safe checkout boundary"
            )
        if slot_id and address_id in (None, ""):
            raise InvalidRequest("slot_id requires address_id")
        cart = self._cart_provider(store).real_cart()
        total = as_decimal(cart.get("total"))
        lines = cart.get("lines") or cart.get("items") or []
        if (
            not total.is_finite()
            or total <= 0
            or total > cap
            or not isinstance(lines, list)
            or not lines
        ):
            raise InvalidRequest(
                f"cart must be non-empty, verifiable and no greater than {money(cap)} EUR"
            )

        selected_slot: dict[str, Any] | None = None
        delivery_verified = False
        if address_id not in (None, ""):
            addresses = self._delivery_provider(store).delivery_addresses()
            if not any(str(row.get("id")) == str(address_id) for row in addresses):
                raise InvalidRequest("address_id is not a current saved address")
            slots = self._delivery_provider(store).delivery_slots(address_id)
            if slot_id:
                selected_slot = next(
                    (row for row in slots if str(row.get("id")) == str(slot_id)),
                    None,
                )
                if (
                    selected_slot is None
                    or not selected_slot.get("available")
                    or not selected_slot.get("open", True)
                ):
                    raise InvalidRequest("selected delivery slot is not currently available")
                delivery_verified = True

        return self._public_plan(
            {
                "store": store,
                "handoff_stage": "verified_cart",
                "ready_for_human_review": True,
                "cart": cart,
                "selected_slot": selected_slot,
                "verified_total_text": money(total),
                "max_total_text": money(cap),
                "cart_verified": True,
                "delivery_verified": delivery_verified,
                "checkout_verified": False,
                "safe_checkout_boundary_available": False,
                "requires_manual_delivery_selection": not delivery_verified,
                "automated_order_submission": False,
                "human_final_action_required": True,
                "private_review_url_exposed": False,
            }
        )

    def open_human_review(
        self,
        *,
        store: str,
        max_total: float,
        checkout_id: str | None = None,
        address_id: str | int | None = None,
        slot_id: str | None = None,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Validate the handoff and open a visible window without clicking."""

        if timeout_seconds < 30 or timeout_seconds > 900:
            raise InvalidRequest("timeout_seconds must be between 30 and 900")
        handoff = self.prepare_human_handoff(
            store=store,
            max_total=max_total,
            checkout_id=checkout_id,
            address_id=address_id,
            slot_id=slot_id,
        )
        provider = self._handoff_provider(store)
        window = provider.open_human_review(
            checkout_id=checkout_id,
            checkout_review=handoff["handoff_stage"] == "checkout_review",
            timeout_seconds=timeout_seconds,
        )
        return self._public_plan(
            {
                **handoff,
                "window": window,
                "automated_order_submission": False,
                "order_outcome": "not_observed_by_automation",
            }
        )

    def prepare_delivery_selection(self, *, store: str, checkout_id: str, address_id: str | int, slot_id: str, max_total: float) -> dict[str, Any]:
        cap = as_decimal(max_total)
        if cap <= 0:
            raise InvalidRequest('max_total must be greater than zero')
        slots = self._delivery_provider(store).delivery_slots(address_id)
        selected = next((slot for slot in slots if str(slot.get('id')) == str(slot_id)), None)
        if selected is None or not selected.get('available') or not selected.get('open', True):
            raise InvalidRequest('selected delivery slot is not currently available')
        checkout = self._checkout_provider(store).get_checkout(checkout_id)
        total = as_decimal(checkout.get('total'))
        projected_total = self._projected_delivery_total(
            store=store,
            checkout=checkout,
            address_id=address_id,
            selected=selected,
        )
        if total <= 0 or total > cap or projected_total > cap:
            raise InvalidRequest('checkout plus selected delivery fee exceeds max_total')
        summary = self._public_plan({'store': store, 'checkout': checkout, 'address_id': address_id, 'slot': selected, 'max_total': float(cap), 'state_changed': False})
        return self.confirmations.create(action='delivery_select', phrase=f'CONFIRMAR ENTREGA {slot_id}', payload={'store': store, 'checkout_id': checkout_id, 'address_id': address_id, 'slot_id': slot_id, 'max_total': float(cap)}, summary=summary)

    def commit_delivery_selection(self, confirmation_id: str, confirmation_phrase: str) -> dict[str, Any]:
        _require_retailer_writes()
        payload = self.confirmations.consume(confirmation_id, confirmation_phrase, action='delivery_select')
        store = str(payload['store'])
        address_id = payload['address_id']
        slot_id = str(payload['slot_id'])
        cap = as_decimal(payload['max_total'])
        slots = self._delivery_provider(store).delivery_slots(address_id)
        selected = next((slot for slot in slots if str(slot.get('id')) == slot_id), None)
        if selected is None or not selected.get('available') or not selected.get('open', True):
            raise InvalidRequest('selected delivery slot is no longer available')
        checkout = self._checkout_provider(store).get_checkout(str(payload['checkout_id']))
        total = as_decimal(checkout.get('total'))
        projected_total = self._projected_delivery_total(
            store=store,
            checkout=checkout,
            address_id=address_id,
            selected=selected,
        )
        if total <= 0 or total > cap or projected_total > cap:
            raise InvalidRequest('checkout plus selected delivery fee exceeds max_total')
        return self._checkout_provider(store).set_checkout_delivery(str(payload['checkout_id']), address_id=address_id, slot_id=slot_id, max_total=cap)

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
        summary = self._public_plan({'store': store, 'checkout': checkout, 'authorized_total_text': money(total), 'order_placed': False, 'local_approval_code_required': True})
        return self.confirmations.create(action='order_submit', phrase=f'COMPRAR {money(total)} EUR', payload={'store': store, 'checkout_id': checkout_id, 'max_total': float(cap), 'expected_total': float(total)}, summary=summary)

    def submit_order(self, confirmation_id: str, confirmation_phrase: str, approval_code: str) -> dict[str, Any]:
        _require_retailer_writes()
        _require_order_submission_enabled()
        _require_order_approval(approval_code)
        payload = self.confirmations.consume(confirmation_id, confirmation_phrase, action='order_submit')
        store = str(payload['store'])
        checkout_id = str(payload['checkout_id'])
        self._validated_checkout_delivery(store, checkout_id)
        checkout = self._checkout_provider(store).get_checkout(checkout_id)
        total = as_decimal(checkout.get('total'))
        cap = as_decimal(payload['max_total'])
        expected_total = as_decimal(payload.get('expected_total'))
        if (
            total <= 0
            or total > cap
            or total.quantize(Decimal('0.01'))
            != expected_total.quantize(Decimal('0.01'))
        ):
            raise InvalidRequest('checkout total changed after explicit confirmation')
        return self._checkout_provider(store).submit_order(checkout_id, max_total=cap)
