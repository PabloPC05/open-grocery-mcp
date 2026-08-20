# Provider contract

The provider model separates public catalogue reads from authenticated writes.

## Catalogue contract

Every store implements `GroceryProvider.search`; `product()` and `categories()` are optional. A provider must return normalized products, keep money as `Decimal`, resolve location before claiming location-dependent prices, declare only implemented capabilities, raise domain errors and avoid returning secrets or unredacted personal data.

## Optional authenticated contracts

### `AuthenticatedCartProvider`

- account status;
- browser-session import/login;
- real-cart read;
- cart-update preview;
- confirmed cart commit.

### `DeliveryProvider`

- redacted saved addresses;
- current delivery slots.

### `CheckoutProvider`

- checkout preview and creation;
- checkout read;
- delivery selection;
- separately gated order submission.

## Write requirements

A commit receives a reviewed plan with exact retailer product identities and quantities. It must:

- enforce a hard total cap;
- detect concurrent cart changes where possible;
- verify the remote result after a write;
- fail closed when a non-empty total cannot be verified;
- attempt rollback after a failed/over-budget cart mutation;
- keep checkout creation and order submission separate;
- never automate bank authentication;
- never accept credentials or cookies as ordinary tool parameters;
- include fixture-based tests for normalization and safety policies.

## Browser-driven providers

A browser provider may use rendered controls when authenticated write endpoints are private or unstable. It must additionally:

- use a visible browser for login;
- store the Playwright session locally with owner-only permissions;
- restrict product navigation to the retailer domain;
- distinguish add/continue controls from the final submit control;
- keep private checkout URL tokens out of MCP responses;
- stop on ambiguous or missing controls;
- require a second browser-specific opt-in for the irreversible submit click.

Selectors should be based on accessible names, roles and resilient structural fallbacks, not a single minified CSS class. A provider is implementation-complete when these contracts exist and are tested with fixtures; live compatibility and real-transaction verification must be reported separately.
