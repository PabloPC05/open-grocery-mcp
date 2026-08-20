# Contributing

## Provider rules

A new provider must:

1. Implement `GroceryProvider.search` and declare only verified capabilities.
2. Accept an injected `httpx.Client` so tests never require live retailer calls.
3. Normalize prices with `Decimal`, currencies and units (`kg`, `L`, `u`).
4. Resolve location before returning location-dependent prices.
5. Raise domain errors rather than returning silent empty data after HTTP failures.
6. Include fixture-based tests for successful and failed responses.
7. Keep catalogue reads separate from authentication and cart writes.

Authenticated cart support must be proposed separately. It may not place an
order or initiate payment without an explicit, narrowly scoped confirmation
mechanism and a security review.

## Development

```bash
python -m pip install -e ".[dev]"
pytest
python -m compileall -q src tests
ruff check .
```

Open a focused pull request and document whether a provider was tested against a
live storefront, fixtures, or both. Never commit cookies, tokens, addresses,
passwords or captured payment traffic.
