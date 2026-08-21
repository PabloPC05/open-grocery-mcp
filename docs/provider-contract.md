# Provider contract

The provider model separates public catalogue reads from authenticated writes.

## Catalogue contract

Every store implements `GroceryProvider.search`; `product()` and `categories()` are optional. A provider must return normalized products, keep money as `Decimal`, resolve location before claiming location-dependent prices, declare only implemented capabilities, raise domain errors and avoid returning secrets or unredacted personal data.

### Optional public delivery policy

A provider may implement:

```python
def delivery_coverage(postal_code: str) -> dict[str, object]: ...
```

Only use this capability when the retailer exposes a verified public source. The normalized result may contain:

- the assortment/store ID serving the postal code;
- listed delivery cost;
- minimum order amount;
- free-delivery threshold.

`compare_basket` may include these values in its estimated checkout total, but must continue to distinguish the product subtotal from the estimate. Personal coupons, loyalty discounts and checkout-time substitutions remain excluded.

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

## Migrating a browser provider to HTTP

A sanitized capture may be used to replace browser cart/checkout operations with a retailer-specific HTTP client. Before enabling a write method, the implementation must verify:

- session cookie/token source and refresh behavior;
- CSRF/XSRF requirements;
- public site/store/location context;
- exact request and response body schemas;
- cart version or other concurrency semantics;
- authoritative total after the operation;
- error and expiration behavior.

Endpoint strings extracted from JavaScript bundles are leads, not sufficient evidence for enabling writes. Fixture tests must be based on an observed, sanitized request/response contract.
