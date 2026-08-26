# HTTP contract capture for Gadis, Froiz, Mercadona and Eroski

This diagnostic is the migration path from Playwright-driven shopping to the lighter Mercadona-style architecture:

```text
visible browser login -> value-free request capture -> retailer-specific HTTP client
```

It never sends a final order request. Potential order/payment routes are blocked, and the local `order_submit_probe` phase additionally aborts **every** non-idempotent request before it leaves Chromium.

## What it inspects

1. Storefront bootstrap and location/session calls.
2. Login request shape.
3. Add-to-cart.
4. Cart read.
5. Quantity `1 -> 2 -> 1`.
6. Product removal.
7. Saved addresses.
8. Delivery slots.
9. Checkout creation/navigation.
10. Delivery selection.
11. The shape of the final submission request, captured while blocked.

Use a low-cost non-restricted grocery product. Alcohol, tobacco, vape and nicotine products must not be used.

## Sanitization

Traffic is converted directly into structured events in memory. The capture does **not** write a raw HAR.

The generated JSON preserves phases, methods, route structure, header names, status codes and body schemas while removing:

- passwords and secrets;
- `Authorization`, cookie, API-key and CSRF/XSRF values;
- access, refresh and session tokens;
- email addresses and phone numbers;
- customer, user, account, address, cart, checkout and order identifiers, including short numeric route IDs;
- street addresses and postal codes;
- payment, card and bank fields;
- URL fragments and all query values.

Public catalogue identifiers such as product, category, site and store IDs remain available because they are needed to reproduce the contract.

## Recommended: local interactive capture

The first GitHub-hosted guest run exposed an important limitation of remote inspection:

- Froiz returned HTTP `403` to the datacenter runner for cart paths.
- the legacy Gadis `super.gadisline.com` entry point was not resolvable from that runner; the current probe now starts at `www.gadisline.com`.

A capture from the account owner's own connection is therefore more representative and is the preferred route for authenticated analysis.

Install the browser tooling:

```bash
python -m pip install -e ".[dev,browser]"
playwright install chromium
```

Run Gadis:

```bash
python tools/capture_http_local.py \
  --store gadis \
  --output local-captures/gadis.json
```

Run Froiz:

```bash
python tools/capture_http_local.py \
  --store froiz \
  --output local-captures/froiz.json
```

A visible browser opens with a black Open Grocery panel. Enter the disposable account credentials **only in the retailer page**. Before each manual action, select and mark the corresponding phase. Press **Finalizar** when complete.

The browser session is saved separately, with owner-only permissions, under:

```text
~/.open-grocery-mcp/gadis/storage_state.json
~/.open-grocery-mcp/froiz/storage_state.json
```

That session file is not included in the capture. Treat it like a password and delete or revoke it after the experiment.

The output JSON is sanitized before being written and receives an `endpoint_manifest` automatically. Review it before sharing because retailers can introduce unforeseen fields.

### Safe final-request probe

Immediately before clicking the final retailer control, select:

```text
11 · SONDA BLOQUEADA del pedido final
```

In that phase every `POST`, `PUT`, `PATCH` and `DELETE` is recorded as a value-free schema and aborted with `blockedbyclient`. Known order/payment URLs are blocked in every phase. The browser may show an error after the click; that is expected. No retry is required.

## Supplementary GitHub Actions capture

The committed [`tools/capture-request.json`](../tools/capture-request.json) triggers [the capture workflow](../.github/workflows/capture-http-contract.yml) on `feat/initial-mcp`.

Guest example:

```json
{"store": "all", "mode": "guest"}
```

Authenticated GitHub-hosted capture uses disposable credentials stored as repository Actions secrets, never in source, issues, pull requests or chat:

```text
GADIS_TEST_USERNAME
GADIS_TEST_PASSWORD
FROIZ_TEST_USERNAME
FROIZ_TEST_PASSWORD
```

Then change the request to:

```json
{"store": "all", "mode": "authenticated"}
```

This mode is useful for repeatability, but a retailer may block datacenter IPs. In that case use the local interactive capture instead.

The disposable account should have:

- no saved payment method;
- no active orders;
- no loyalty balance or coupons of value;
- a unique password not used anywhere else;
- only addresses the tester is authorized to use.

## Published diagnostics

GitHub-hosted captures publish only compact value-free manifests under:

```text
diagnostics/http-contracts/
```

Detailed sanitized artifacts are retained for 14 days by GitHub Actions. Local captures remain solely on the user's machine unless explicitly shared.

## Turning a capture into an HTTP provider

For each retailer:

1. Identify session cookies, CSRF headers and refresh/bootstrap calls.
2. Map cart reads and add/set/remove request schemas.
3. Determine site/store/location identifiers and concurrency/version semantics.
4. Map addresses, slots and checkout creation.
5. Implement fixture tests before enabling live writes.
6. Retain Playwright only for login, CAPTCHA or anti-bot cookie renewal when required.
7. Keep final order submission disabled until a deliberate transaction validates it.

The target architecture is:

```text
login/anti-bot in Playwright -> cookies/tokens -> HTTP client -> cart and checkout
```
