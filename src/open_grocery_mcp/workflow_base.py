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
    OrderSubmissionDisabled,
    RetailerWritesDisabled,
    UnsupportedOperation,
)
from open_grocery_mcp.models import as_decimal
from open_grocery_mcp.providers.base import (
    AuthenticatedCartProvider,
    CheckoutProvider,
    DeliveryProvider,
    HumanHandoffProvider,
)
from open_grocery_mcp.providers.browser_normalize import is_restricted_product, sanitize_url


def _enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def _require_retailer_writes() -> None:
    if not _enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        raise RetailerWritesDisabled(
            "retailer writes are disabled; set OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 "
            "only on the user's own local MCP process"
        )


def _require_order_approval(approval_code: str) -> None:
    configured = os.getenv("OPEN_GROCERY_ORDER_APPROVAL_CODE", "")
    if len(configured) < 6:
        raise OrderApprovalRequired(
            "configure OPEN_GROCERY_ORDER_APPROVAL_CODE with at least 6 characters"
        )
    if not hmac.compare_digest(configured, approval_code):
        raise OrderApprovalRequired("local order approval code is incorrect")


def _require_order_submission_enabled() -> None:
    if not _enabled("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION"):
        raise OrderSubmissionDisabled(
            "order submission is disabled; set OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1 "
            "only on the user's own local MCP process"
        )


class WorkflowBase:
    """Shared workflow dependencies and policy checks."""

    def __init__(self, registry: Any, drafts: Any, confirmations: ConfirmationStore) -> None:
        self.registry = registry
        self.drafts = drafts
        self.confirmations = confirmations

    def _cart_provider(self, store: str) -> AuthenticatedCartProvider:
        provider = self.registry.get(store)
        if (
            "real_cart" not in provider.info.capabilities
            or not isinstance(provider, AuthenticatedCartProvider)
        ):
            raise UnsupportedOperation(f"{provider.info.label} has no authenticated cart support")
        return provider

    def _delivery_provider(self, store: str) -> DeliveryProvider:
        provider = self.registry.get(store)
        if (
            "delivery" not in provider.info.capabilities
            or not isinstance(provider, DeliveryProvider)
        ):
            raise UnsupportedOperation(f"{provider.info.label} has no delivery-slot support")
        return provider

    def _checkout_provider(self, store: str) -> CheckoutProvider:
        provider = self.registry.get(store)
        if (
            "checkout" not in provider.info.capabilities
            or not isinstance(provider, CheckoutProvider)
        ):
            raise UnsupportedOperation(f"{provider.info.label} has no checkout support")
        return provider

    def _handoff_provider(self, store: str) -> HumanHandoffProvider:
        provider = self.registry.get(store)
        if (
            "human_handoff" not in provider.info.capabilities
            or not isinstance(provider, HumanHandoffProvider)
        ):
            raise UnsupportedOperation(
                f"{provider.info.label} has no visible human handoff support"
            )
        return provider

    @staticmethod
    def _public_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
        private = {
            "desired_items",
            "desired_lines",
            "previous_items",
            "previous_lines",
            "cart_payload",
        }

        def public_value(value: Any, *, key: str = "") -> Any:
            if isinstance(value, Mapping):
                return {
                    str(child_key): public_value(child_value, key=str(child_key))
                    for child_key, child_value in value.items()
                    if str(child_key) not in private
                    and not str(child_key).startswith("_")
                }
            if isinstance(value, list):
                return [public_value(item) for item in value]
            if key.casefold().endswith("url"):
                return sanitize_url(value)
            return deepcopy(value)

        return public_value(plan)

    @staticmethod
    def _draft_changes(draft: Mapping[str, Any], store: str) -> list[dict[str, Any]]:
        basket = draft.get("basket", {})
        if not isinstance(basket, Mapping) or basket.get("store") != store:
            raise InvalidRequest("draft belongs to a different supermarket")
        if not basket.get("complete"):
            raise InvalidRequest(
                "draft is incomplete; resolve required missing items before writing"
            )
        details = basket.get("details", [])
        changes: list[dict[str, Any]] = []
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, Mapping) or not detail.get("found"):
                continue
            product = detail.get("product", {})
            request = detail.get("request", {})
            if not isinstance(product, Mapping) or not isinstance(request, Mapping):
                continue
            product_id = str(product.get("id", "")).strip()
            name = str(product.get("name", "")).strip()
            category = str(product.get("category", "")).strip()
            quantity = as_decimal(request.get("quantity", 1))
            if quantity <= 0:
                raise InvalidRequest(
                    f"draft contains an invalid quantity for {name or product_id!r}"
                )
            unit_price = as_decimal(product.get("price"))
            url = sanitize_url(product.get("url"))
            if is_restricted_product(name, category):
                raise InvalidRequest(
                    f"automated purchase of age-restricted product {name!r} is not supported"
                )
            if (product_id or name or url) and quantity > 0:
                change: dict[str, Any] = {
                    "product_id": product_id,
                    "quantity": float(quantity),
                }
                if name:
                    change["name"] = name
                if category:
                    change["category"] = category
                if unit_price > 0:
                    change["unit_price"] = float(unit_price)
                if url:
                    change["url"] = url
                changes.append(change)
        if not changes:
            raise InvalidRequest("draft contains no matched products")
        return changes

    def account_status(self, store: str) -> dict[str, Any]:
        return self._cart_provider(store).account_status()

    def import_browser_session(self, store: str, storage_state_path: str) -> dict[str, Any]:
        return self._cart_provider(store).import_browser_session(storage_state_path)

    def login_with_browser(self, store: str, timeout_seconds: int = 300) -> dict[str, Any]:
        return self._cart_provider(store).login_with_browser(timeout_seconds=timeout_seconds)

    def real_cart(self, store: str) -> dict[str, Any]:
        return self._cart_provider(store).real_cart()
