"""Composite Froiz catalogue and browser-authenticated provider."""

from __future__ import annotations

from decimal import Decimal
import threading
import time
from typing import Any, Mapping, Sequence

from open_grocery_mcp.errors import AuthenticationRequired, ProviderError
from open_grocery_mcp.models import Product, StoreInfo, as_decimal
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.froiz import FroizProvider
from open_grocery_mcp.providers.froiz_account import FroizAccountClient
from open_grocery_mcp.providers.froiz_pricing import normalize_pricing

_AUTH_CATALOGUE_RETRY_SECONDS = 60


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
            "human_handoff",
        ),
        requires_postal_code=False,
        price_scope=(
            "online search catalogue plus the location selected in the logged-in "
            "Froiz session"
        ),
        notes=(
            "Search prefers the authenticated, location-aware Nuxt catalogue and "
            "falls back to Froiz's public Empathy.co index when that session route "
            "is unavailable. Cart reads, "
            "whole-object reversible mutations, saved addresses and the "
            "delivery calendar use the verified Nuxt HTTP contract. Froiz has "
            "no separately verified checkout step: orders/create places the "
            "real order, so checkout and order submission remain unavailable."
        ),
    )

    def __init__(
        self,
        *,
        catalogue: FroizProvider | None = None,
        account: FroizAccountClient | None = None,
    ) -> None:
        self._catalogue = catalogue or FroizProvider()
        self._account = account or FroizAccountClient()
        self._auth_catalogue_retry_after = 0.0
        self._auth_catalogue_lock = threading.Lock()

    @staticmethod
    def _authenticated_product(raw: Mapping[str, Any]) -> Product | None:
        product_id = str(raw.get("id") or raw.get("product_id") or "").strip()
        name = str(raw.get("name") or raw.get("title") or "").strip()
        price_value: Any = (
            raw.get("order_price")
            if raw.get("order_price") not in (None, "")
            else raw.get("base_price")
            if raw.get("base_price") not in (None, "")
            else raw.get("price")
        )
        if isinstance(price_value, Mapping):
            price_value = (
                price_value.get("value")
                or price_value.get("amount")
                or price_value.get("price")
            )
        price = as_decimal(price_value)
        if not product_id or not name or price <= 0:
            return None
        slug = str(raw.get("slug") or product_id).strip()
        unit = str(raw.get("unit") or raw.get("measurementUnit") or "").strip()
        price_per_unit = as_decimal(raw.get("price_per_unit"))
        return Product(
            store="froiz",
            id=product_id,
            name=name,
            price=price,
            currency="EUR",
            price_per_unit=price_per_unit if price_per_unit > 0 else None,
            unit=unit or None,
            available=raw.get("enabled") is not False,
            url=f"https://supermercado.froiz.com/product/{slug}",
            metadata={
                "location_aware": True,
                "catalogue_backend": "froiz_authenticated",
                **normalize_pricing(raw, price_source="authenticated.order_price"),
            },
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        with self._auth_catalogue_lock:
            try_authenticated = time.monotonic() >= self._auth_catalogue_retry_after
        if try_authenticated:
            try:
                authenticated_rows = self._account.search_products(
                    query,
                    limit=limit,
                    postal_code=postal_code,
                )
                authenticated = [
                    product
                    for row in authenticated_rows
                    if isinstance(row, Mapping)
                    for product in [self._authenticated_product(row)]
                    if product is not None
                ]
                if authenticated:
                    with self._auth_catalogue_lock:
                        self._auth_catalogue_retry_after = 0.0
                    return authenticated[: max(1, min(limit, 100))]
            except (AuthenticationRequired, ProviderError):
                with self._auth_catalogue_lock:
                    self._auth_catalogue_retry_after = (
                        time.monotonic() + _AUTH_CATALOGUE_RETRY_SECONDS
                    )
        return self._catalogue.search(
            query,
            limit=limit,
            postal_code=postal_code,
            eco=eco,
        )

    def catalogue_contract(self) -> dict[str, Any]:
        contract = self._catalogue.catalogue_contract()
        return {
            **contract,
            "geography": "authenticated_session_location_or_public_global_fallback",
            "cache_safe": False,
            "cache_reason": "authenticated location can change outside the query parameters",
        }

    def account_status(self) -> dict[str, Any]:
        return self._account.status()

    def import_browser_session(self, storage_state_path: str) -> dict[str, Any]:
        result = self._account.import_storage_state(storage_state_path)
        with self._auth_catalogue_lock:
            self._auth_catalogue_retry_after = 0.0
        return result

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        result = self._account.login_with_browser(timeout_seconds=timeout_seconds)
        with self._auth_catalogue_lock:
            self._auth_catalogue_retry_after = 0.0
        return result

    def real_cart(self) -> dict[str, Any]:
        return self._account.real_cart()

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

    def open_human_review(
        self,
        *,
        checkout_id: str | None = None,
        checkout_review: bool = False,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        return self._account.open_human_review(
            checkout_id=checkout_id,
            checkout_review=checkout_review,
            timeout_seconds=timeout_seconds,
        )

    def close(self) -> None:
        self._catalogue.close()
        self._account.close()
