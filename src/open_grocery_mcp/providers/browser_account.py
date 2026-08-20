"""Authenticated browser account implementing the common retailer protocols."""

from __future__ import annotations

from open_grocery_mcp.providers.browser_account_cart import BrowserAccountCartMixin
from open_grocery_mcp.providers.browser_account_checkout import BrowserAccountCheckoutMixin
from open_grocery_mcp.providers.browser_account_state import BrowserAccountStateMixin


class BrowserAccountClient(
    BrowserAccountCartMixin,
    BrowserAccountCheckoutMixin,
    BrowserAccountStateMixin,
):
    """Compose state, cart and checkout behavior for one browser retailer."""
