"""Composite Mercadona catalogue and authenticated-account provider."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.mercadona import MercadonaProvider
from open_grocery_mcp.providers.mercadona_account import MercadonaAccountClient


class MercadonaFullProvider(GroceryProvider):
    """Expose the catalogue and the optional authenticated workflow as one store."""

    info = StoreInfo(
        key="mercadona",
        label="Mercadona",
        country="ES",
        languages=("es",),
        capabilities=(
            "search",
            "product",
            "categories",
            "compare",
            "draft_cart",
            "account",
            "real_cart",
            "delivery",
            "checkout",
            "order_submission_experimental",
        ),
        requires_postal_code=True,
        price_scope="warehouse serving the supplied Spanish postal code",
        notes=(
            "Catalogue reads are public. Authenticated writes require a local browser "
            "session, a two-phase confirmation and explicit server opt-ins."
        ),
    )

    def __init__(self) -> None:
        self._catalogue = MercadonaProvider()
        self._account = MercadonaAccountClient()

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        return self._catalogue.search(
            query,
            limit=limit,
            postal_code=postal_code,
            eco=eco,
        )

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        return self._catalogue.product(product_id, postal_code=postal_code)

    def categories(
        self,
        *,
        depth: int = 1,
        postal_code: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._catalogue.categories(depth=depth, postal_code=postal_code)

    def account_status(self) -> dict[str, Any]:
        return self._account.status()

    def import_browser_session(self, storage_state_path: str) -> dict[str, Any]:
        return self._account.import_storage_state(storage_state_path)

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        return self._account.login_with_browser(timeout_seconds=timeout_seconds)

    def real_cart(self) -> dict[str, Any]:
        return self._account.cart()

    def preview_cart_update(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        return self._account.preview_cart_update(
            changes,
            mode=mode,
            expected_version=expected_version,
            max_total=max_total,
        )

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        return self._account.commit_cart_update(plan)

    def delivery_addresses(self) -> list[dict[str, Any]]:
        return self._account.addresses()

    def delivery_slots(self, address_id: str | int) -> list[dict[str, Any]]:
        return self._account.slots(address_id)

    def preview_checkout(
        self,
        *,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        return self._account.preview_checkout(
            expected_version=expected_version,
            max_total=max_total,
        )

    def create_checkout(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        return self._account.create_checkout(plan)

    def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        return self._account.get_checkout(checkout_id)

    def set_checkout_delivery(
        self,
        checkout_id: str,
        *,
        address_id: str | int,
        slot_id: str,
        max_total: Decimal,
    ) -> dict[str, Any]:
        return self._account.set_checkout_delivery(
            checkout_id,
            address_id=address_id,
            slot_id=slot_id,
            max_total=max_total,
        )

    def submit_order(self, checkout_id: str, *, max_total: Decimal) -> dict[str, Any]:
        return self._account.submit_order(checkout_id, max_total=max_total)

    def close(self) -> None:
        self._catalogue.close()
        self._account.close()
