# Security policy

## Current boundary

Version `0.1.x` is read-only against retailer systems. Cart drafts are kept only
in the running process and contain no account credentials or payment data.

## Reporting

Do not open a public issue containing credentials, cookies, personal addresses
or exploitable retailer details. Contact the repository owner privately through
GitHub before publishing a proof of concept.

## Remote deployment

The Streamable HTTP transport has no application-level authentication in this
release. Bind to localhost or place it behind a private network/authenticated
reverse proxy. Apply rate limits and never share one authenticated retailer
session among multiple users in future cart-enabled versions.
