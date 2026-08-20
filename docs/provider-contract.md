# Provider contract

The provider model separates public catalogue reads from authenticated writes.

## Catalogue contract

Every store implements `GroceryProvider.search`:

```python
def search(
    query: str,
    *,
    limit: int = 10,
    postal_code: str | None = None,
    eco: bool = False,
) -> list[Product]:
    ...
```

`product()` and `categories()` are optional. A provider must:

1. Return normalized `Product` objects.
2. Keep money as `Decimal` internally.
3. Resolve location before claiming location-dependent prices.
4. Declare only capabilities that are implemented.
5. Accept an injected HTTP client so tests can avoid live calls.
6. Raise domain errors for failed retailer requests.
7. Avoid returning credentials or unredacted personal data.

## Optional authenticated contracts

A store may separately implement:

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

- checkout preview;
- checkout creation;
- checkout read;
- delivery selection;
- gated order submission.

The MCP checks these protocols at runtime. Unsupported stores fail explicitly.

## Write requirements

A provider commit must not accept raw search terms. It receives a reviewed plan
containing exact retailer product IDs and quantities. It must:

- enforce a hard total cap;
- detect concurrent cart changes where possible;
- verify the remote result after a write;
- fail closed when the total cannot be verified;
- never combine checkout creation and order submission;
- never automate bank authentication;
- include fixture-based tests for all request/response shapes.

Authenticated endpoints must first be observed in the user's own legitimate web
session. Never commit captured tokens, cookies, addresses or payment traffic.
