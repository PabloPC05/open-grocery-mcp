"""Authenticated retailer workflows assembled from focused mixins."""

from open_grocery_mcp.workflow_base import WorkflowBase
from open_grocery_mcp.workflow_cart import CartWorkflowMixin
from open_grocery_mcp.workflow_checkout import CheckoutWorkflowMixin


class RetailerWorkflowService(CartWorkflowMixin, CheckoutWorkflowMixin, WorkflowBase):
    """Two-phase authenticated shopping workflow service."""
