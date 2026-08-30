"""Composite Eroski provider: browser session first, HTTP contract next.

The Eroski storefront is a server-rendered Apache Tapestry 5 application.
Public catalogue results are server-rendered and readable without a session.
Authenticated cart forms use ``POST /es/search/...`` with ``q`` and a signed
``t:formdata`` token bound to the user's ``JSESSIONID``; the provider therefore
combines HTTP reads with browser mutations and verifies the result over HTTP.

Safety boundary discovered during recon: Eroski has no separate checkout
creation step — ``orders/create``-style endpoints place real orders — so
checkout/order automation stays disabled here by design.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    ConcurrentCartChange,
    ProviderError,
)
from open_grocery_mcp.models import Product, StoreInfo, as_decimal
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import EROSKI_BROWSER_CONFIG
from open_grocery_mcp.providers.browser_normalize import parse_money_text
from open_grocery_mcp.providers.eroski_catalogue import EroskiCatalogueProvider
from open_grocery_mcp.providers.eroski_delivery import EroskiDeliveryClient
from open_grocery_mcp.providers.eroski_http import EroskiHTTPClient
from open_grocery_mcp.providers import eroski_ui


class EroskiFullProvider(GroceryProvider):
    info = StoreInfo(
        key="eroski",
        label="Eroski",
        country="ES",
        languages=("es",),
        capabilities=(
            "search",
            "product",
            "compare",
            "draft_cart",
            "login",
            "account",
            "real_cart",
            "cart_read",
            "cart_write",
            "addresses",
            "slots",
            "delivery",
            "human_handoff",
        ),
        requires_postal_code=False,
        price_scope=(
            "public online catalogue; authenticated cart data follows the delivery "
            "context already selected in the Eroski session"
        ),
        notes=(
            "Catalogue search parses Eroski's public server-rendered results. Account, "
            "cart mutations run in the user's locally stored browser session. "
            "Eroski is a server-rendered Tapestry app whose "
            "cart forms require a signed t:formdata token bound to JSESSIONID, "
            "and its order endpoint places real orders (no separate checkout). "
            "Delivery is GET-only: addresses can be listed and slots are returned "
            "only for the address already selected in the session. Selecting another "
            "address/store is never hidden inside a read. Checkout and order submission "
            "are unavailable by design."
        ),
    )

    def __init__(self) -> None:
        self._catalogue = EroskiCatalogueProvider()
        self._account = BrowserAccountClient(EROSKI_BROWSER_CONFIG)
        self._http = EroskiHTTPClient()
        self._delivery = EroskiDeliveryClient(http=self._http)

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

    def catalogue_contract(self) -> dict[str, Any]:
        return self._catalogue.catalogue_contract()

    def account_status(self) -> dict[str, Any]:
        browser = self._account.status()
        http = self._http.status()
        return {
            **browser,
            **http,
            "authenticated_session": bool(http.get("authenticated")),
            "validated_live": bool(http.get("authenticated")),
            "account_backend": "eroski_browser_with_http_validation",
        }

    def import_browser_session(self, storage_state_path: str) -> dict[str, Any]:
        result = self._account.import_storage_state(storage_state_path)
        self._http.invalidate_session()
        return {**result, **self.account_status()}

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        result = self._account.login_with_browser(timeout_seconds=timeout_seconds)
        self._http.invalidate_session()
        return {**result, **self.account_status()}

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
                "lines": [
                    {"product_id": i.product_id, "quantity": i.quantity}
                    for i in cart.items
                ],
                "products_count": len(cart.items),
                "total": float(cart.total),
                "total_text": cart.total_text,
                "currency": "EUR",
            }
        except (AuthenticationRequired, ProviderError) as exc:
            fallback = self._account.cart()
            return {
                **fallback,
                "cart_backend": "browser",
                "browser_driven": True,
                "http_fallback_reason": type(exc).__name__,
            }

    @staticmethod
    def _http_browser_carts_match(
        http_cart: Mapping[str, Any], browser_cart: Mapping[str, Any]
    ) -> bool:
        http_lines = http_cart.get("lines", [])
        browser_lines = browser_cart.get("lines", [])
        if not isinstance(http_lines, list) or not isinstance(browser_lines, list):
            return False
        left = sorted(
            (
                str(line.get("product_id") or "").strip(),
                as_decimal(line.get("quantity")),
            )
            for line in http_lines
            if isinstance(line, Mapping)
        )

        right = sorted(
            (
                str(line.get("product_id") or "").strip(),
                as_decimal(line.get("quantity")),
            )
            for line in browser_lines
            if isinstance(line, Mapping)
        )
        if not all(product_id for product_id, _ in left + right) or left != right:
            return False
        http_total = as_decimal(http_cart.get("total")) or parse_money_text(
            http_cart.get("total_text")
        )
        browser_total = as_decimal(browser_cart.get("total"))
        return http_total > 0 and http_total == browser_total

    def preview_cart_update(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        current = self.real_cart()
        current_version = int(current.get("version") or 0)
        if expected_version is not None and current_version != expected_version:
            raise ConcurrentCartChange(
                "Eroski cart changed after it was read; prepare the update again"
            )
        if current.get("cart_backend") == "browser":
            plan = self._account.preview_cart_update(
                changes,
                mode=mode,
                expected_version=current_version,
                max_total=max_total,
            )
            return {
                **plan,
                "plan_backend": "eroski_browser_fallback",
                "http_fallback_reason": current.get("http_fallback_reason"),
            }

        browser_cart = self._account.cart()
        if not self._http_browser_carts_match(current, browser_cart):
            raise ConcurrentCartChange(
                "Eroski HTTP and browser carts do not match; inspect the cart before writing"
            )
        plan = self._account.preview_cart_update(
            changes,
            mode=mode,
            expected_version=int(browser_cart.get("version") or 0),
            max_total=max_total,
        )
        return {
            **plan,
            "expected_http_cart_version": current_version,
            "plan_backend": "eroski_browser",
        }

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        backend = str(plan.get("plan_backend") or "")
        if backend == "eroski_browser_fallback":
            return self._account.commit_cart_update(plan)
        if backend != "eroski_browser":
            raise ConcurrentCartChange("unknown Eroski cart plan backend; review again")
        expected_http = int(plan.get("expected_http_cart_version") or 0)
        current_http = self._http.read_cart().version
        if current_http != expected_http:
            raise ConcurrentCartChange(
                "Eroski cart changed after review; prepare the update again"
            )
        result = self._account.commit_cart_update(plan)
        desired = [
            line
            for line in plan.get("desired_lines", [])
            if isinstance(line, Mapping)
        ]
        expected_lines = sorted(
            (
                str(line.get("product_id") or "").strip(),
                as_decimal(line.get("quantity")),
            )
            for line in desired
        )
        if not all(product_id for product_id, _ in expected_lines):
            raise ProviderError(
                "Eroski browser update cannot be verified over HTTP without product ids"
            )
        try:
            observed = self._http.read_cart()
        except (AuthenticationRequired, ProviderError) as exc:
            raise ProviderError(
                "Eroski browser update completed but its HTTP result could not be "
                "verified; inspect the cart before any further write"
            ) from exc
        actual_lines = sorted(
            (item.product_id, as_decimal(item.quantity)) for item in observed.items
        )
        expected_total = as_decimal(plan.get("estimated_total"))
        if actual_lines != expected_lines or observed.total.quantize(
            Decimal("0.01")
        ) != expected_total.quantize(Decimal("0.01")):
            raise ProviderError(
                "Eroski HTTP cart does not match the verified browser result; "
                "inspect the cart before any further write"
            )
        return {
            **result,
            "http_post_write_verified": True,
            "http_cart_version": observed.version,
        }

    def delivery_addresses(self) -> list[dict[str, Any]]:
        return self._delivery.delivery_addresses()

    def delivery_slots(self, address_id: str | int) -> list[dict[str, Any]]:
        return self._delivery.delivery_slots(address_id)

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

    def add_item_via_browser(
        self,
        query: str = "leche",
        *,
        tile_index: int = 0,
        max_price: Decimal = Decimal("5.00"),
        expected_product_ref: str | None = None,
    ) -> dict[str, Any]:
        """UI-driven add while the Tapestry zone binding is replicated."""
        ui = eroski_ui.ui_context(
            getattr(self._account, "state_path", "")
            or str(getattr(self._http, "state_path", ""))
        )
        try:
            return eroski_ui.add_first_result(
                ui,
                query,
                tile_index=tile_index,
                max_price=max_price,
                expected_product_ref=expected_product_ref,
            )
        finally:
            ui["close"]()

    def remove_item_via_browser(
        self, product_id: str, *, max_clicks: int = 6
    ) -> dict[str, Any]:
        """UI-driven removal of one basket row by product id."""
        ui = eroski_ui.ui_context(
            getattr(self._account, "state_path", "")
            or str(getattr(self._http, "state_path", ""))
        )
        try:
            return eroski_ui.remove_product(ui, product_id, max_clicks=max_clicks)
        finally:
            ui["close"]()

    def close(self) -> None:
        self._catalogue.close()
        self._http.close()
        self._account.close()
