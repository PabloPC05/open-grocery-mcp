# Security policy

## Current boundary

Open Grocery MCP `0.2.x` can access an authenticated Mercadona account when the
owner imports a local browser session. The default process remains read-only:
retailer mutations and order submission require separate local opt-ins.

## Sensitive files

The Mercadona `storage_state.json` can contain access tokens, refresh tokens and
cookies. Treat it like a password.

- Keep it outside the repository.
- Restrict it to the operating-system user.
- Do not paste tokens, cookies, passwords, addresses or card data into MCP tools.
- Revoke the session from the retailer account if the file is exposed.

The default path is:

```text
~/.open-grocery-mcp/mercadona/storage_state.json
```

## Write protections

Authenticated writes require `OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`. Every
write is split into prepare and commit calls, with a short-lived one-use ID,
exact phrase, version check and hard spending limit.

Final order submission additionally requires:

- `OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1`;
- `OPEN_GROCERY_ORDER_APPROVAL_CODE` with at least six characters;
- a checkout with an address and still-available delivery slot;
- a fresh authoritative total below the approved cap.

The final endpoint is experimental and has not been validated by placing a real
order from this repository. Do not enable it until you have reviewed the code and
intend to make a deliberate purchase.

## Remote deployment

Do not expose Streamable HTTP directly to the public Internet. Version `0.2.x`
has no application-level identity, tenant isolation or persistent distributed
confirmation store. Bind to localhost or place it behind a private network and
an authenticated TLS proxy. Run a single process per user/session.

## Reporting

Do not open a public issue containing credentials, cookies, personal addresses,
checkout identifiers or exploitable retailer details. Contact the repository
owner privately through GitHub before publishing a proof of concept.
