"""Composite Eroski provider: browser session first, HTTP contract next.

The Eroski storefront is a server-rendered Apache Tapestry 5 application.
Guest recon established the cart form contract (``POST /es/search/...``
``productlistadditem.form`` with ``q`` and the signed ``t:formdata`` token,
anonymous basket at ``/es/login/anonymousbasket/?basketType=ALI``) but every
meaningful read requires an authenticated session, so this provider starts
browser-backed like Froiz did before its HTTP migration.

Safety boundary discovered during recon: Eroski has no separate checkout
creation step — ``orders/create``-style endpoints place real orders — so
checkout/order automation stays disabled here by design.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import EROSKI_BROWSER_CONFIG
from open_grocery_mcp.providers.froiz import FroizProvider


class EroskiFullProvider(GroceryProvider):
    info = StoreInfo(
        key="eroski",
        label="Eroski",
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
        ),
        requires_postal_code=True,
        price_scope=(
            "online catalogue plus the delivery area selected in the logged-in "
            "Eroski session"
        ),
        notes=(
            "Catalogue search reuses Froiz's Empathy.co index shape. Account, "
            "cart, delivery and checkout run in the user's locally stored "
            "browser session: Eroski is a server-rendered Tapestry app whose "
            "cart forms require a signed t:formdata token bound to JSESSIONID, "
            "and its order endpoint places real orders (no separate checkout), "
            "so automated checkout stays disabled."
        ),
    )

    def __init__(self) -> None:
        self._catalogue = FroizProvider()
        self._account = BrowserAccountClient(EROSKI_BROWSER_CONFIG)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        postal_code: str | None = None,
        eco: bool = False,
    ) -> list[Product]:
        return self._catalogue.search(
            query, limit=limit, postal_code=postal_code, eco=eco
        )

    def product(self, product_id: str, *, postal_code: str | None = None) -> Product:
        return self._catalogue.product(product_id, postal_code=postal_code)

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

    def close(self) -> None:
        self._catalogue.close()
        self._account.close()
