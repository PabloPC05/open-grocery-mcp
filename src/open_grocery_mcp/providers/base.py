"""Provider contracts implemented by supermarket adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from open_grocery_mcp.errors import UnsupportedOperation
from open_grocery_mcp.models import Product, StoreInfo


class GroceryProvider(ABC):
    """Read-only catalogue interface shared by every supermarket."""

    info: StoreInfo

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        """Search a store-specific catalogue."""

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        raise UnsupportedOperation(f"{self.info.label} does not expose product detail")

    def search_page(
        self,
        query: str,
        *,
        page_size: int = 100,
        cursor: str | None = None,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> dict[str, Any]:
        """Return a catalogue page plus explicit coverage evidence.

        Providers without a verified cursor contract expose one bounded page and
        never invent a total or ``has_next`` value.
        """

        if cursor is not None:
            raise UnsupportedOperation(
                f"{self.info.label} does not expose verified catalogue pagination"
            )
        products = self.search(
            query,
            limit=max(1, min(page_size, 100)),
            postal_code=postal_code,
            eco=eco,
        )
        return {
            "products": products,
            "next_cursor": None,
            "has_next": None,
            "total": None,
            "pagination": "bounded_unknown",
        }

    def catalogue_contract(self) -> dict[str, Any]:
        return {
            "pagination": "bounded_unknown",
            "maximum_page_size": 100,
            "exact_total": False,
            "category_search": False,
            "geography": "provider_specific",
            "cache_safe": True,
        }

    def categories(
        self,
        *,
        depth: int = 1,
        postal_code: str | None = None,
    ) -> list[dict[str, Any]]:
        raise UnsupportedOperation(f"{self.info.label} does not expose categories")

    def close(self) -> None:
        """Release HTTP resources. Providers without resources may ignore this."""


@runtime_checkable
class AuthenticatedCartProvider(Protocol):
    """Optional account and real-cart capability."""

    info: StoreInfo

    def account_status(self) -> dict[str, Any]: ...

    def import_browser_session(self, storage_state_path: str) -> dict[str, Any]: ...

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]: ...

    def real_cart(self) -> dict[str, Any]: ...

    def preview_cart_update(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]: ...

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class DeliveryProvider(Protocol):
    """Optional saved-address and delivery-slot capability."""

    info: StoreInfo

    def delivery_addresses(self) -> list[dict[str, Any]]: ...

    def delivery_slots(self, address_id: str | int) -> list[dict[str, Any]]: ...


@runtime_checkable
class CheckoutProvider(Protocol):
    """Optional checkout preparation and gated order-submission capability."""

    info: StoreInfo

    def preview_checkout(
        self,
        *,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]: ...

    def create_checkout(self, plan: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_checkout(self, checkout_id: str) -> dict[str, Any]: ...

    def set_checkout_delivery(
        self,
        checkout_id: str,
        *,
        address_id: str | int,
        slot_id: str,
        max_total: Decimal,
    ) -> dict[str, Any]: ...

    def submit_order(self, checkout_id: str, *, max_total: Decimal) -> dict[str, Any]: ...


@runtime_checkable
class HumanHandoffProvider(Protocol):
    """Optional visible, zero-click handoff to the retailer UI."""

    info: StoreInfo

    def open_human_review(
        self,
        *,
        checkout_id: str | None = None,
        checkout_review: bool = False,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]: ...
