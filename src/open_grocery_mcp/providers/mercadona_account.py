"""Authenticated Mercadona account client assembled from focused mixins."""

from open_grocery_mcp.providers.mercadona_browser import MercadonaBrowserMixin
from open_grocery_mcp.providers.mercadona_cart import MercadonaCartMixin
from open_grocery_mcp.providers.mercadona_cart_commit import MercadonaCartCommitMixin
from open_grocery_mcp.providers.mercadona_checkout import MercadonaCheckoutMixin
from open_grocery_mcp.providers.mercadona_http import MercadonaHTTPMixin
from open_grocery_mcp.providers.mercadona_state import MercadonaSession, MercadonaStateClient


class MercadonaAccountClient(
    MercadonaCartCommitMixin,
    MercadonaCartMixin,
    MercadonaCheckoutMixin,
    MercadonaHTTPMixin,
    MercadonaBrowserMixin,
    MercadonaStateClient,
):
    """One authenticated Mercadona account, cart and checkout workflow."""


__all__ = ["MercadonaAccountClient", "MercadonaSession"]
