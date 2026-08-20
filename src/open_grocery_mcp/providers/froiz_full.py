"""Composite Froiz catalogue and browser-authenticated provider."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import FROIZ_BROWSER_CONFIG
from open_grocery_mcp.providers.froiz import FroizProvider


class FroizFullProvider(GroceryProvider):
    info = StoreInfo(
        key="froiz",
        label="Froiz",
        country="ES",
        languages=("es",),
        capabilities=(
            "search",
            "compare",
            "draft_cart",
            "account",
            "real_cart",
            "delivery",
            "checkout",
            "order_submission_experimental",
        ),
        requires_postal_code=False,
        price_scope=(
            "online search catalogue plus the location selected in the logged-in "
            "Froiz session"
        ),
        notes=(
            "Catalogue search uses Froiz's public search index. Account, cart and "
            "checkout use the user's locally stored browser session and rendered controls."
        ),
    )

    def __init__(self) -> None:
        self._catalogue = FroizProvider()
        self._account = BrowserAccountClient(FROIZ_BROWSER_CONFIG)

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

    def submit_order(
        self,
        checkout_id: str,
        *,
        max_total: Decimal,
    ) -> dict[str, Any]:
        return self._account.submit_order(checkout_id, max_total=max_total)

    def close(self) -> None:
        self._catalogue.close()
        self._account.close()
