# Authenticated workflows

Authenticated operations are optional capabilities, separate from catalogue reads. All three built-in stores implement the same MCP workflow, but the execution backend differs:

- Mercadona: authenticated HTTP API.
- Gadis: local Playwright session and visible Gadisline controls.
- Froiz: local Playwright session and visible Froiz controls.

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

A browser storefront may need to navigate through intermediate pages to reveal addresses or slots. That navigation never authorizes the final order. Checkout creation, delivery selection and order submission remain distinct confirmed operations.

## Confirmation properties

A pending confirmation contains the private plan in process memory and exposes only a reviewable summary. It is random-ID addressed, action-bound, phrase-bound, valid for five minutes, consumed once and invalid after process restart.

A client must show the summary to the user. It must not call a commit tool merely because the prepare response contains the expected phrase.

## Cart concurrency and budget

Mercadona uses the retailer cart version. Browser-driven providers create a deterministic fingerprint from product identity, quantity, price and total. Before committing, the provider re-reads the cart and rejects a changed fingerprint.

After a write, every provider reads the cart again and checks the exact reviewed line set. A positive authoritative total is required for a non-empty cart. If the total exceeds the cap or cannot be verified, the browser provider attempts to restore the previous lines and reports failure.

## Browser login

`login_with_browser` opens a visible Chromium/Chrome window. The user enters credentials directly on the retailer site and completes any challenge. For Gadis/Froiz the page injects a black **Open Grocery: guardar sesión** button; clicking it saves Playwright storage state locally. No password is accepted by an MCP tool.

The browser backend:

1. loads the stored session;
2. opens the retailer cart or checkout through accessible labels and link fallbacks;
3. captures relevant JSON responses when present;
4. otherwise normalizes rendered product rows, totals, addresses and slots;
5. performs changes only through visible controls;
6. verifies the resulting page/cart state.

It refuses product URLs outside the configured retailer domain and never exposes query tokens from private checkout URLs.

## Final order

All providers require:

```text
OPEN_GROCERY_ENABLE_RETAILER_WRITES=1
OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1
OPEN_GROCERY_ORDER_APPROVAL_CODE=<local secret>
```

Gadis and Froiz additionally require:

```text
OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1
```

The caller then supplies the one-use confirmation ID, exact total-bound phrase and local approval code. The checkout, address, slot and total are re-read immediately before submission.

Payment or strong customer authentication may still occur in the retailer or bank interface. Open Grocery reports `requires_user_action` rather than attempting to bypass it.


## Browser-provider retry policy

Gadis and Froiz record a local submission-attempt marker immediately before the
final click. If the resulting page cannot prove success or failure, the checkout
is left in an unverified state and the MCP refuses automatic retry. The user must
inspect retailer order history first.
