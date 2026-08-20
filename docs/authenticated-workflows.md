# Authenticated workflows

Authenticated operations are optional capabilities, not part of the catalogue
interface. A provider may implement search without gaining any method that can
mutate a retailer account.

## State machine

```text
catalogue
   ↓
local draft
   ↓
prepare cart plan ── no remote write
   ↓ exact one-use confirmation
commit cart ──────── remote cart write
   ↓
prepare checkout ─── no remote write
   ↓ exact one-use confirmation
create checkout
   ↓
prepare delivery ─── no remote write
   ↓ exact one-use confirmation
set delivery
   ↓
prepare order ────── authoritative total + live slot check
   ↓ phrase + local approval code + two environment opt-ins
submit order ─────── irreversible
```

## Confirmation properties

A pending confirmation contains the full private plan in process memory and
returns only a reviewable summary. It is:

- random-ID addressed;
- bound to one action;
- bound to one exact phrase;
- valid for five minutes;
- consumed once;
- invalid after process restart.

A client must present the summary to the user. It must not call a commit tool
merely because the prepare response contains the expected phrase.

## Cart concurrency and budget

Mercadona carts include a version. The provider checks that the version reviewed
by the user still matches immediately before the PUT. After writing it polls the
cart until the remote line set equals the approved line set. It then reads the
authoritative total. If that total exceeds the cap, it attempts to restore the
previous lines and reports failure rather than proceeding.

The cap is checked again when creating checkout, selecting delivery and sending
the order. This matters because promotions, weighted products and delivery fees
can make the retailer total differ from the catalogue estimate.

## Browser login

`login_with_browser` opens a visible Chrome session using Playwright. The user
enters credentials directly into the retailer page and completes any challenge.
The MCP observes successful authenticated traffic and stores Playwright's
`storage_state.json`. The password is never returned to or accepted by an MCP
tool.

## Final order

`submit_order` is intentionally difficult to enable. The operator must set:

```text
OPEN_GROCERY_ENABLE_RETAILER_WRITES=1
OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1
OPEN_GROCERY_ORDER_APPROVAL_CODE=<local secret>
```

The caller then supplies the one-use confirmation ID, the exact total-bound
phrase and the separate local approval code. The provider re-reads checkout and
delivery state immediately before submitting.

Payment or strong customer authentication may still occur outside this MCP. The
project does not attempt to bypass it.
