"""Two-phase real-cart workflows."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from open_grocery_mcp.models import as_decimal
from open_grocery_mcp.workflow_base import _require_retailer_writes


class CartWorkflowMixin:
    """Two-phase real-cart workflows."""

    def prepare_cart_update(self, *, store: str, draft_id: str, max_total: float, expected_cart_version: int | None, mode: str = 'merge') -> dict[str, Any]:
        cap = as_decimal(max_total)
        draft = self.drafts.get(draft_id)
        changes = self._draft_changes(draft, store)
        plan = self._cart_provider(store).preview_cart_update(changes, mode=mode, expected_version=expected_cart_version, max_total=cap)
        phrase = f"CONFIRMAR CARRITO {plan['estimated_total_text']} EUR"
        return self.confirmations.create(action='cart_update', phrase=phrase, payload={'store': store, 'plan': plan}, summary=self._public_plan(plan))

    def prepare_clear_cart(self, *, store: str, expected_cart_version: int | None) -> dict[str, Any]:
        plan = self._cart_provider(store).preview_cart_update([], mode='replace', expected_version=expected_cart_version, max_total=Decimal('0.01'))
        return self.confirmations.create(action='cart_update', phrase='VACIAR CARRITO', payload={'store': store, 'plan': plan}, summary=self._public_plan(plan))

    def commit_cart_update(self, confirmation_id: str, confirmation_phrase: str) -> dict[str, Any]:
        _require_retailer_writes()
        payload = self.confirmations.consume(confirmation_id, confirmation_phrase, action='cart_update')
        provider = self._cart_provider(str(payload['store']))
        return provider.commit_cart_update(payload['plan'])
