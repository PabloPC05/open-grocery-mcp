# Contributing

## Provider rules

A new catalogue provider must implement the contract in
[`docs/provider-contract.md`](docs/provider-contract.md), accept an injected HTTP
client and include fixture-based tests.

Authenticated support must be proposed as a separate capability. A contribution
must document whether each endpoint was verified against:

- public unauthenticated traffic;
- a simulated fixture;
- an authenticated browser session;
- or an intentional live transaction.

Do not describe an endpoint as end-to-end verified unless the corresponding
operation actually completed. Final order tests must use a deliberate purchase
owned and approved by the tester; they must never run in CI.

Never commit:

- passwords;
- bearer or refresh tokens;
- cookies or browser storage state;
- full addresses;
- checkout/order identifiers tied to a person;
- payment-card or bank-authentication data.

## Development

```bash
python -m pip install -e ".[dev,browser]"
pytest
python -m compileall -q src tests
ruff check .
```

Keep pull requests focused and preserve attribution for incorporated MIT code.
