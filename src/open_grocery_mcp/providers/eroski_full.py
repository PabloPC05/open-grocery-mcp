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
from open_grocery_mcp.providers.eroski_http import EroskiHTTPClient
from open_grocery_mcp.providers import eroski_ui
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
        self._http = EroskiHTTPClient()

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
        try:
            cart = self._http.read_cart()
            return {
                "store": "eroski",
                "cart_backend": "eroski_http",
                "browser_driven": False,
                "version": cart.version,
                "items": [
                    {"product_id": i.product_id, "quantity": i.quantity}
                    for i in cart.items
                ],
                "products_count": len(cart.items),
                "total_text": cart.total_text,
                "currency": "EUR",
            }
        except Exception as exc:
            fallback = self._account.cart()
            return {
                **fallback,
                "cart_backend": "browser",
                "browser_driven": True,
                "http_fallback_reason": type(exc).__name__,
            }

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

    def add_item_via_browser(self, query: str = "leche") -> dict[str, Any]:
        """UI-driven add while the Tapestry zone binding is replicated."""
        ui = eroski_ui.ui_context(
            getattr(self._account, "state_path", "")
            or str(getattr(self._http, "state_path", ""))
        )
        try:
            return eroski_ui.add_first_result(ui, query)
        finally:
            ui["close"]()

    def remove_item_via_browser(self, product_id: str) -> dict[str, Any]:
        """UI-driven removal of one basket row by product id."""
        ui = eroski_ui.ui_context(
            getattr(self._account, "state_path", "")
            or str(getattr(self._http, "state_path", ""))
        )
        try:
            return eroski_ui.remove_product(ui, product_id)
        finally:
            ui["close"]()

    def close(self) -> None:
        self._catalogue.close()
        self._http.close()
        self._account.close()
