# Authenticated workflows

Authenticated operations are optional capabilities, separate from catalogue reads. The four built-in stores share the cart workflow, but only expose delivery or checkout when the retailer has a verified safe boundary:

- Mercadona: authenticated HTTP for cart, delivery and checkout.
- Gadis: authenticated HTTP for whole-unit cart operations, delivery and a reversible checkout summary that reaches the card-review page; the payment-bearing checkout POST stays blocked.
- Froiz: Nuxt HTTP client for cart and delivery with browser fallback; the local verifier uses an explicit write gate, never retries a mutation, rereads authoritatively and requires exact restoration; checkout/order unavailable by design.
- Eroski: HTTP cart reads plus verified browser cart writes. Its advertised GET-only delivery reader inspects only the context already selected in the session and fails closed for another address; checkout/order are unavailable by design.

## Froiz local reversible verification

The live Froiz probe requires an authenticated local session. The initial
`POST` deliberately creates an empty cart and the probe item is added only
after the cart identity and channel binding have been verified. When the
`shop` channel initially has no cart, the verifier records that baseline,
requires the exact new empty cart to be the only bound cart, and finally
deletes it and requires `/api/me` to return to `cartId: null`. When a cart was
already bound, the verifier requires that original identity and content to
remain unchanged. Any unrelated rebind or concurrent content stops the flow.

Run it only with a disposable test account, no saved payment method, and no
order-submission opt-ins enabled:

```powershell
$env:OPEN_GROCERY_ENABLE_RETAILER_WRITES = "1"
Remove-Item Env:OPEN_GROCERY_ENABLE_ORDER_SUBMISSION -ErrorAction SilentlyContinue
Remove-Item Env:OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION -ErrorAction SilentlyContinue

python .\tools\verify_froiz_cart_local.py `
  --allow-reversible-cart-write `
  --max-added-value 5.00
```

The session is read from `~/.open-grocery-mcp/froiz/storage_state.json`, unless
`OPEN_GROCERY_FROIZ_STATE_PATH` is set. The probe reads addresses and delivery
slots, creates a disposable cart, verifies quantity `1 → 2 → 1`, removes the
line and, only after stable exact rereads, deletes the disposable cart before
rereading the original channel cart. If exact cleanup preconditions cannot be
proved, it stops without another write. A
successful report requires `channel_cart_untouched=true`, all cart steps true,
`retailer_write_performed=true`, and `ok=true`.

The probe chooses a simple packaged item from the authenticated,
store-specific `GET /api/products` catalogue rather than the global public
index. Froiz exposes two cart reads: `/api/cart/raw/{id}` is authoritative for
the replacement payload but has no product enrichment or totals, while
`/api/cart/{id}` independently returns prices, product subtotal and a total
that may include delivery fees. Verification uses the processed subtotal for
the reviewed product cap and preserves the raw optional `units` field.

`order_or_payment_attempted=false` is a code-level allowlist guarantee, not
network telemetry: `FroizHTTPClient` only exposes profile, delivery, raw-cart,
cart-create/update/delete routes, and the Froiz account client blocks checkout
and order methods because `orders/create` is the real-order boundary. The
probe does not claim to have observed every outbound request unless a separate
sanitized capture records it.

## Mercadona local verification

Mercadona has a read-only mode and a separately gated reversible cart mode:

```powershell
python .\tools\verify_mercadona_local.py

$env:OPEN_GROCERY_ENABLE_RETAILER_WRITES = "1"
python .\tools\verify_mercadona_local.py --allow-reversible-cart-write
```

Read-only verification requires a live session, an authoritative cart,
addresses and delivery slots and reports zero retailer mutations. The optional
probe adds one ordinary product worth at most EUR 5, rereads the cart and
restores the exact initial fingerprint. It never calls checkout, order,
payment, Redsys or 3-D Secure routes.

## Eroski local closure

Eroski login is the only browser step. Run it with a disposable account and
complete credentials or 2FA only in the visible retailer window; the command
prints status booleans, never the storage state:

```powershell
python -c "from open_grocery_mcp.providers.eroski_full import EroskiFullProvider as P; p=P(); r=p.login_with_browser(timeout_seconds=300); print({k:r.get(k) for k in ('session_saved','authenticated_session','validated_live','http_session_checked')}); p.close()"
```

Check the persisted session from a fresh HTTP client process. This performs
read-only cart requests and persists an official-domain `JSESSIONID` rotation
atomically after a successful cart read:

```powershell
python -c "from open_grocery_mcp.providers.eroski_full import EroskiFullProvider as P; p=P(); r=p.account_status(); print({k:r.get(k) for k in ('authenticated_session','validated_live','http_session_checked','authenticated')}); p.close()"
```

The reversible cart probe requires both explicit local gates. It adds one
ordinary absent product for at most EUR 5, rereads it, removes it once, and
stops for manual inspection after an ambiguous write or concurrent change; it
never calls checkout, order, payment or Redsys routes:

```powershell
$env:OPEN_GROCERY_ENABLE_RETAILER_WRITES = "1"
Remove-Item Env:OPEN_GROCERY_ENABLE_ORDER_SUBMISSION -ErrorAction SilentlyContinue
Remove-Item Env:OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION -ErrorAction SilentlyContinue
python .\tools\verify_eroski_cart_local.py `
  --allow-reversible-cart-write `
  --max-added-value 5.00
Remove-Item Env:OPEN_GROCERY_ENABLE_RETAILER_WRITES -ErrorAction SilentlyContinue
```

The delivery observer is GET-only by default. It blocks all non-GET retailer
requests, never submits the final slot form, and requires an existing saved
session:

```powershell
Remove-Item Env:OPEN_GROCERY_ENABLE_ORDER_SUBMISSION -ErrorAction SilentlyContinue
Remove-Item Env:OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION -ErrorAction SilentlyContinue
python .\tools\verify_eroski_delivery_local.py
```

`--allow-delivery-read-post` is a separate, opt-in diagnostic for the observed
address-map and selected-saved-address zone POSTs; it is not GET-only and requires
`OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`. It must not be used for the normal
delivery closure.

When a pickup slot is already selected, `--allow-slot-summary-post` permits
exactly the previously captured Tapestry `slotform` field set, reaches
`/es/bookingdeliverysummary/` by GET and then requires the same opaque selected
slot hash after reopening the delivery page. Every other non-GET and all
order/payment routes remain blocked. If Eroski exposes no current slot, the
verifier stops before this transition instead of inventing one.

## State machine

```text
catalogue
   ↓
local draft
   ↓
prepare cart plan ── no retailer write
   ↓ exact one-use confirmation
commit cart ──────── remote cart write
   ↓
prepare checkout ─── no checkout creation
   ↓ exact one-use confirmation
create checkout
   ↓
prepare delivery ─── validate address + live slot
   ↓ exact one-use confirmation
set delivery
   ↓
prepare order ────── current total + delivery recheck
   ↓ phrase + local approval code + environment opt-ins
submit order ─────── irreversible
```

The checkout branch only exists for providers that advertise `checkout`. A browser storefront may need to navigate through intermediate pages to reveal addresses or slots. That navigation never authorizes the final order. Checkout creation, delivery selection and order submission remain distinct confirmed operations.

## Human handoff

`prepare_human_handoff` provides one common completion contract for all four
stores without weakening their retailer-specific safety boundary:

- Mercadona/Gadis: require an existing checkout, re-read its positive total,
  enforce the caller's cap, require a saved address and live delivery slot, and
  return `handoff_stage=checkout_review`.
- Froiz/Eroski: re-read a non-empty cart and enforce the cap. If the caller
  supplies an address and slot, both are revalidated; the result remains
  `handoff_stage=verified_cart` because no safe pre-order checkout write exists.

`open_human_review` performs the same validation before launching visible
Chromium with the private local session. It uses direct GET navigation and zero
automated clicks. It never selects payment, submits an order, observes the
human's final result or exposes a private checkout URL. A human may continue in
that window; the MCP reports `order_outcome=not_observed_by_automation`.

Gadis HTTP summary records are stored in the same owner-only local checkout
store as browser records, but with an explicit `gadis_http` backend marker and
without a URL or credentials. A process restart therefore preserves the
summary/cart continuity. Re-reading compares product identity, quantity,
price and total against the authoritative HTTP cart. Delivery changes and order
submission for that HTTP checkout fail closed and are left to the human window.

## Confirmation properties

A pending confirmation contains the private plan in process memory and exposes only a reviewable summary. It is random-ID addressed, action-bound, phrase-bound, valid for five minutes, consumed once and invalid after process restart.

A client must show the summary to the user. It must not call a commit tool merely because the prepare response contains the expected phrase.

## Cart concurrency and budget

HTTP providers use a retailer version or a deterministic content fingerprint. Browser-driven providers create a deterministic fingerprint from product identity, quantity, price and total. Before committing, every provider re-reads the cart and rejects a changed fingerprint.

After a write, every provider reads the cart again and checks the exact reviewed
line set, unit prices and total whenever the retailer exposes them. A positive
authoritative total is required for a non-empty cart. An ambiguous write is never
retried automatically: a safe read must prove success or prove that nothing
changed. If the observed state matches neither the reviewed plan nor the prior
state, it is never overwritten automatically because it may contain a concurrent
change; further writes require manual inspection. Cleanup is not a retry of
the failed mutation: it is allowed only for a known probe/disposable identity
after stable rereads prove the exact expected probe-only state. Otherwise it
is skipped. A newly created disposable cart may be removed only after those
identity and state checks.

## Browser login

`login_with_browser` opens a visible Chromium/Chrome window. The user enters credentials directly on the retailer site and completes any challenge. The local helper saves Playwright storage state without exposing credentials to an MCP tool. Froiz validates an exact authenticated `GET /api/me`; Gadis validates the signed-in `GET /api/auth/session`; Eroski requires an account-only logout/disconnect control; Mercadona requires successful customer and cart GETs from the trusted storefront. A changed URL, generic welcome message or guest cookie is never accepted as authentication evidence.

The browser backend:

1. loads the stored session;
2. opens the retailer cart or checkout through accessible labels and link fallbacks;
3. captures relevant JSON responses when present;
4. otherwise normalizes rendered product rows, totals, addresses and slots;
5. performs changes only through visible controls;
6. verifies the resulting page/cart state.

It refuses product URLs outside the configured retailer domain and never exposes query tokens from private checkout URLs.

## Final order

Providers that actually expose final order submission require:

```text
OPEN_GROCERY_ENABLE_RETAILER_WRITES=1
OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1
OPEN_GROCERY_ORDER_APPROVAL_CODE=<local secret>
```

Any browser-backed final click additionally requires:

```text
OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1
```

The caller then supplies the one-use confirmation ID, exact total-bound phrase and local approval code. The checkout, address, slot and total are re-read immediately before submission.

Payment or strong customer authentication may still occur in the retailer or bank interface. Open Grocery reports `requires_user_action` rather than attempting to bypass it.


## Retry policy

An order-capable provider records a local submission-attempt marker immediately
before the final request or click. If the result cannot be proved, the checkout
is left in an unverified state and the MCP refuses automatic retry. Froiz and
Eroski do not expose checkout/order operations because their observed boundary
would already create the real order.
