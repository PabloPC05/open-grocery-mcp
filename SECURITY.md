# Security policy

## Current boundary

Open Grocery MCP `0.5.x` can use authenticated accounts for Mercadona, Gadis, Froiz and Eroski. The default process remains read-only: retailer writes and order submission require separate local opt-ins.

Mercadona uses authenticated HTTP calls. Gadis and Froiz use HTTP clients with local Playwright for login and explicit fallbacks. Eroski uses HTTP reads and browser cart writes that are verified again over HTTP. Browser automation does not weaken the confirmation or spending-limit requirements.

## Sensitive files

Every `storage_state.json` can contain cookies, access tokens or local storage equivalent to a signed-in account. Treat it like a password.

Default locations include:

```text
~/.open-grocery-mcp/gadis/storage_state.json
~/.open-grocery-mcp/froiz/storage_state.json
~/.open-grocery-mcp/eroski/storage_state.json
~/.open-grocery-mcp/mercadona/storage_state.json
```

- Keep sessions outside the repository.
- Restrict them to the operating-system user.
- Do not paste tokens, cookies, passwords, addresses or card data into MCP tools.
- Revoke the retailer session if a file is exposed.
- Do not run one process/session for multiple unrelated users.

Session, token-cache and private checkout files are written through unique
same-directory temporary files, protected for the operating-system owner before
publication, and cleaned if serialization or atomic replacement fails. Browser
login reuses a structurally valid local state when available without exposing
its contents.

Checkout records may contain a private URL with a short-lived token. It is stored locally with owner-only permissions and never returned by an MCP tool.

## Write protections

Every state-changing operation is split into prepare and commit calls with:

- a random one-use confirmation ID;
- an exact phrase;
- a five-minute expiry;
- a reviewed cart version or fingerprint;
- a hard spending cap;
- verification after the write;
- attempted rollback on a failed or over-budget cart update.

Final order submission additionally requires:

```text
OPEN_GROCERY_ENABLE_RETAILER_WRITES=1
OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1
OPEN_GROCERY_ORDER_APPROVAL_CODE=<local secret>
```

Browser-driven final submission additionally requires:

```text
OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1
```

No provider automates bank authentication, PSD2, 3-D Secure, SMS codes or biometrics. Age-restricted products are rejected from automated cart plans.

## Browser-specific boundary

Selectors for browser-backed providers, including Gadis, Froiz and Eroski, are adaptive but cannot be proved stable against every future storefront release. When the page cannot be read, a control is ambiguous or the resulting total cannot be verified, the provider fails closed. It must never convert a missing selector into a successful purchase.

The browser workflow is intended for a local `stdio` MCP process. A remotely hosted browser would place account sessions on that server and should be avoided unless the operator has deliberately secured and isolated it.

## Remote Vercel boundary

The production Streamable HTTP endpoint at `https://open-grocery-mcp.vercel.app/mcp`
is **publicly accessible without authentication**. This is an intentional product
decision: the service provides read-only access to catalogue, comparison, coverage,
and offers.

The Vercel deployment must keep all retailer-write and order-submission feature
flags unset. It contains no retailer storage state, browser profile, address,
checkout record or payment information. Authenticated/browser workflows remain
local even though the catalogue MCP can run remotely. `main` is the production
branch; Git previews also provide public read-only access.

## Transaction verification

Code and fixture tests are not the same as a real purchase. Do not describe final order placement as live-verified until the owner deliberately completes a low-value order and records only redacted results. Never run order submission in CI.

## Ambiguous submission results

Before a browser provider clicks the irreversible final control, it records a
local submission-attempt marker. A crash or ambiguous response therefore blocks
automatic retries. The operator must inspect the retailer order history before
any further action, preventing duplicate orders.

## Reporting

Do not open a public issue containing credentials, cookies, personal addresses, checkout identifiers, private checkout URLs or exploitable retailer details. Contact the repository owner privately before publishing a proof of concept.
