"""Domain errors exposed by Open Grocery MCP."""

from __future__ import annotations


class OpenGroceryError(Exception):
    """Base class for predictable, user-facing failures."""


class InvalidRequest(OpenGroceryError):
    """The caller supplied an invalid tool argument."""


class StoreNotFound(OpenGroceryError):
    """A requested supermarket adapter is not registered."""


class UnsupportedOperation(OpenGroceryError):
    """The selected supermarket does not implement an operation."""


class ProviderError(OpenGroceryError):
    """A retailer endpoint returned an unexpected response."""


class LocationRequired(OpenGroceryError):
    """A provider needs a location before it can return honest prices."""


class CoverageError(OpenGroceryError):
    """The retailer does not serve the requested location."""
