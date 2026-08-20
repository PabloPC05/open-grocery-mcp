"""Authenticated MCP tools registered separately from catalogue tools."""

from __future__ import annotations

from typing import Any

def register_authenticated_tools(mcp, workflows):

    @mcp.tool()
    def account_status(store: str) -> dict[str, Any]:
        """Report whether a usable local browser session exists for a store."""
        return workflows.account_status(store)

    @mcp.tool()
    def import_browser_session(store: str, storage_state_path: str) -> dict[str, Any]:
        """Import a browser storage_state file by local path.

    Do not paste bearer tokens, cookies or passwords into tool arguments. The
    file is validated and copied into the MCP's private local state directory.
    """
        return workflows.import_browser_session(store, storage_state_path)

    @mcp.tool()
    def login_with_browser(store: str, timeout_seconds: int = 300) -> dict[str, Any]:
        """Open a visible local browser so the user can sign in and complete 2FA."""
        return workflows.login_with_browser(store, timeout_seconds)

    @mcp.tool()
    def get_real_cart(store: str) -> dict[str, Any]:
        """Read the authenticated retailer cart without changing it."""
        return workflows.real_cart(store)

    @mcp.tool()
    def prepare_real_cart_update(store: str, draft_id: str, max_total: float, expected_cart_version: int | None = None, mode: str = 'merge') -> dict[str, Any]:
        """Preview applying a local draft to a real cart and issue a confirmation phrase.

    ``merge`` preserves unrelated existing products; ``replace`` makes the cart
    contain only the reviewed draft. This tool never writes to the retailer.
    """
        return workflows.prepare_cart_update(store=store, draft_id=draft_id, max_total=max_total, expected_cart_version=expected_cart_version, mode=mode)

    @mcp.tool()
    def prepare_clear_real_cart(store: str, expected_cart_version: int | None = None) -> dict[str, Any]:
        """Preview emptying a real cart. Commit requires the exact phrase VACIAR CARRITO."""
        return workflows.prepare_clear_cart(store=store, expected_cart_version=expected_cart_version)

    @mcp.tool()
    def commit_real_cart_update(confirmation_id: str, confirmation_phrase: str) -> dict[str, Any]:
        """Apply one reviewed real-cart update using its exact one-use phrase."""
        return workflows.commit_cart_update(confirmation_id, confirmation_phrase)

    @mcp.tool()
    def list_delivery_addresses(store: str) -> list[dict[str, Any]]:
        """List saved delivery-address IDs with street details redacted."""
        return workflows.delivery_addresses(store)

    @mcp.tool()
    def get_delivery_slots(store: str, address_id: str) -> list[dict[str, Any]]:
        """List currently available delivery windows for a saved address."""
        return workflows.delivery_slots(store, address_id)

    @mcp.tool()
    def prepare_checkout_creation(store: str, max_total: float, expected_cart_version: int | None = None) -> dict[str, Any]:
        """Preview opening a checkout from the current cart without creating it yet."""
        return workflows.prepare_checkout_creation(store=store, max_total=max_total, expected_cart_version=expected_cart_version)

    @mcp.tool()
    def commit_checkout_creation(confirmation_id: str, confirmation_phrase: str) -> dict[str, Any]:
        """Create a reviewed checkout; this still does not place an order."""
        return workflows.commit_checkout_creation(confirmation_id, confirmation_phrase)

    @mcp.tool()
    def get_checkout(store: str, checkout_id: str) -> dict[str, Any]:
        """Read the authoritative checkout total and selected delivery information."""
        return workflows.get_checkout(store, checkout_id)

    @mcp.tool()
    def prepare_delivery_selection(store: str, checkout_id: str, address_id: str, slot_id: str, max_total: float) -> dict[str, Any]:
        """Preview attaching an address and delivery slot to a checkout."""
        return workflows.prepare_delivery_selection(store=store, checkout_id=checkout_id, address_id=address_id, slot_id=slot_id, max_total=max_total)

    @mcp.tool()
    def commit_delivery_selection(confirmation_id: str, confirmation_phrase: str) -> dict[str, Any]:
        """Apply a reviewed delivery selection and recheck the spending cap."""
        return workflows.commit_delivery_selection(confirmation_id, confirmation_phrase)

    @mcp.tool()
    def prepare_order_submission(store: str, checkout_id: str, max_total: float) -> dict[str, Any]:
        """Read the final total and issue the exact one-use purchase phrase.

    This tool does not place an order. The user must explicitly provide the exact
    ``COMPRAR <total> EUR`` phrase after seeing the summary.
    """
        return workflows.prepare_order_submission(store=store, checkout_id=checkout_id, max_total=max_total)

    @mcp.tool()
    def submit_order(confirmation_id: str, confirmation_phrase: str, approval_code: str) -> dict[str, Any]:
        """Place an order after exact confirmation, cap recheck and local approval.

    Requires both retailer-write/order-submit environment opt-ins and the local
    approval code configured on the machine running the MCP. Payment
    authentication may still require the retailer app or bank.
    """
        return workflows.submit_order(confirmation_id, confirmation_phrase, approval_code)
