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

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        operation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.operation = operation


class LocationRequired(OpenGroceryError):
    """A provider needs a location before it can return honest prices."""


class CoverageError(OpenGroceryError):
    """The retailer does not serve the requested location."""


class AuthenticationRequired(OpenGroceryError):
    """An authenticated retailer session is missing or unusable."""


class BudgetExceeded(OpenGroceryError):
    """A proposed cart or checkout exceeds the caller's hard spending cap."""


class ConcurrentCartChange(OpenGroceryError):
    """The remote cart changed after the user reviewed it."""


class ConfirmationRequired(OpenGroceryError):
    """A state-changing action is missing its short-lived confirmation."""


class RetailerWritesDisabled(OpenGroceryError):
    """Authenticated retailer mutations are disabled by server policy."""


class OrderSubmissionDisabled(OpenGroceryError):
    """Irreversible order submission is disabled by server policy."""


class OrderApprovalRequired(OpenGroceryError):
    """The local human approval code is missing or incorrect."""
