# Provider contract

`GroceryProvider` is intentionally a read-only catalogue interface.

## Required

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

A provider must return normalized `Product` objects and must not silently use a
location that could materially change prices. Raise `LocationRequired` when a
postal code or explicit store/warehouse is necessary.

## Optional

- `product(product_id, postal_code=...)`
- `categories(depth=..., postal_code=...)`

The base implementation raises `UnsupportedOperation`, so callers can distinguish
"not implemented" from an empty catalogue.

## Capability separation

Future interfaces will be independent:

- `CartProvider`: login/session and cart mutation.
- `DeliveryProvider`: addresses, slots and substitution preferences.
- `CheckoutPreviewProvider`: final totals before confirmation.
- `OrderProvider`: explicit order submission, disabled by default.

A catalogue provider must never be treated as an order provider through duck
typing. Each capability must be deliberately registered and tested.

## Testing

Use `httpx.MockTransport` or recorded responses stripped of personal data. Tests
must cover HTTP errors, malformed JSON, missing location and product-unit
normalization. Live tests should be optional and must never submit an order.
