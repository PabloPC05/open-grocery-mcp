"""Playwright implementation for authenticated browser workflows."""

from __future__ import annotations

from open_grocery_mcp.providers.browser_driver_cart import BrowserDriverCartMixin
from open_grocery_mcp.providers.browser_driver_checkout import BrowserDriverCheckoutMixin
from open_grocery_mcp.providers.browser_driver_core import BrowserDriverCore


class PlaywrightBrowserDriver(
    BrowserDriverCheckoutMixin,
    BrowserDriverCartMixin,
    BrowserDriverCore,
):
    """Compose browser lifecycle, cart and checkout behavior."""
