"""Shared policy and provider lookup for authenticated workflows."""

from __future__ import annotations

import hmac
import os
from copy import deepcopy
from typing import Any, Mapping

from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.errors import (
    InvalidRequest,
    OrderApprovalRequired,
    RetailerWritesDisabled,
    UnsupportedOperation,
)
from open_grocery_mcp.models import as_decimal
from open_grocery_mcp.providers.base import (
    AuthenticatedCartProvider,
    CheckoutProvider,
    DeliveryProvider,
)


def _enabled(name: str) -> bool:
    return os.getenv(name, '').casefold() in {'1', 'true', 'yes', 'on'}


def _require_retailer_writes() -> None:
    if not _enabled('OPEN_GROCERY_ENABLE_RETAILER_WRITES'):
        raise RetailerWritesDisabled("retailer writes are disabled; set OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 only on the user's own local MCP process")


def _require_order_approval(approval_code: str) -> None:
    configured = os.getenv('OPEN_GROCERY_ORDER_APPROVAL_CODE', '')
    if len(configured) < 6:
        raise OrderApprovalRequired('configure OPEN_GROCERY_ORDER_APPROVAL_CODE with at least 6 characters')
    if not hmac.compare_digest(configured, approval_code):
        raise OrderApprovalRequired('local order approval code is incorrect')


class WorkflowBase:
    """Shared workflow dependencies and policy checks."""

    def __init__(self, registry: Any, drafts: Any, confirmations: ConfirmationStore) -> None:
        self.registry = registry
        self.drafts = drafts
        self.confirmations = confirmations

    def _cart_provider(self, store: str) -> AuthenticatedCartProvider:
        provider = self.registry.get(store)
        if not isinstance(provider, AuthenticatedCartProvider):
            raise UnsupportedOperation(f'{provider.info.label} has no authenticated cart support')
        return provider

    def _delivery_provider(self, store: str) -> DeliveryProvider:
        provider = self.registry.get(store)
        if not isinstance(provider, DeliveryProvider):
            raise UnsupportedOperation(f'{provider.info.label} has no delivery-slot support')
        return provider

    def _checkout_provider(self, store: str) -> CheckoutProvider:
        provider = self.registry.get(store)
        if not isinstance(provider, CheckoutProvider):
            raise UnsupportedOperation(f'{provider.info.label} has no checkout support')
        return provider

    @staticmethod
    def _public_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
        private = {'desired_lines', 'previous_lines', 'cart_payload'}
        return {key: deepcopy(value) for key, value in plan.items() if key not in private}

    @staticmethod
    def _draft_changes(draft: Mapping[str, Any], store: str) -> list[dict[str, Any]]:
        basket = draft.get('basket', {})
        if not isinstance(basket, Mapping) or basket.get('store') != store:
            raise InvalidRequest('draft belongs to a different supermarket')
        if not basket.get('complete'):
            raise InvalidRequest('draft is incomplete; resolve required missing items before writing')
        details = basket.get('details', [])
        changes: list[dict[str, Any]] = []
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, Mapping) or not detail.get('found'):
                continue
            product = detail.get('product', {})
            request = detail.get('request', {})
            if not isinstance(product, Mapping) or not isinstance(request, Mapping):
                continue
            product_id = str(product.get('id', '')).strip()
            quantity = as_decimal(request.get('quantity'), default='1')
            if product_id and quantity > 0:
                changes.append({'product_id': product_id, 'quantity': float(quantity)})
        if not changes:
            raise InvalidRequest('draft contains no matched products')
        return changes

    def account_status(self, store: str) -> dict[str, Any]:
        return self._cart_provider(store).account_status()

    def import_browser_session(self, store: str, storage_state_path: str) -> dict[str, Any]:
        return self._cart_provider(store).import_browser_session(storage_state_path)

    def login_with_browser(self, store: str, timeout_seconds: int = 300) -> dict[str, Any]:
        return self._cart_provider(store).login_with_browser(timeout_seconds=timeout_seconds)

    def real_cart(self, store: str) -> dict[str, Any]:
        return self._cart_provider(store).real_cart()
