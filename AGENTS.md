# Instructions for local agents

These instructions apply to every coding or browser agent working in this repository.

## Mission

Open Grocery MCP should let an agent search products, compare realistic baskets and prepare a supermarket order without tying the project to one city or region. The immediate engineering priority is replacing the Gadis and Froiz browser backends with lightweight authenticated HTTP clients wherever the retailer contract permits it.

A local agent is responsible for work that requires the owner's machine, network, browser profile or authenticated retailer session. Do not turn that work back into a long manual checklist for the owner when the agent has shell or browser automation available.

## Non-negotiable safety rules

1. Never submit a real order or initiate payment while researching or testing an adapter.
2. Never click a final purchase, payment, Redsys, 3-D Secure or bank-confirmation control.
3. During an order-submission probe, block every non-GET request before it leaves Chromium.
4. Never print, copy into chat, commit or upload passwords, cookies, storage state, CSRF/XSRF values, bearer tokens, addresses, phone numbers, email addresses or payment data.
5. `storage_state.json`, `.env`, `local-captures/`, `captures/`, HAR files and raw browser profiles must remain local and ignored by Git.
6. Use a harmless ordinary grocery item, restore its original quantity and remove it after the test.
7. Do not automate regulated products such as alcohol, tobacco, nicotine or vaping products.
8. A failed or ambiguous write is not retried automatically. Read the cart again and diagnose first.
9. Ask the owner only for an unavoidable human-only step, such as unlocking a password manager, completing CAPTCHA/2FA or confirming that a test account may be used. Do not ask the owner to label phases, inspect DevTools, repeat clicks or interpret logs when the agent can do those tasks.

## Repository and branch

Work on `feat/initial-mcp` unless the owner explicitly selects another branch.

```powershell
git fetch origin
git switch feat/initial-mcp
git pull --ff-only origin feat/initial-mcp
```

Before changing code:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
python -m playwright install chromium
pytest
python -m compileall -q src tests tools
```

Do not overwrite unrelated local changes. Inspect `git status` and the relevant diff before committing.

## Ownership split

The agent should do remotely or from public endpoints without involving the owner:

- catalogue, product and category clients;
- delivery coverage, fees and minimum-order logic;
- product normalization and basket matching;
- MCP schemas, confirmation plans, safety checks and tests;
- public bundle/endpoint analysis;
- all code review, linting, unit tests and documentation.

The local agent should do on the owner's machine:

- use an existing authenticated storage state or open the retailer in a visible browser;
- perform login locally without exposing credentials to chat;
- capture and sanitize authenticated cart/address/delivery/checkout traffic;
- diagnose browser selectors, anti-bot behavior and local-only network differences;
- verify that the produced JSON contains real events;
- implement or patch the HTTP adapter from that sanitized contract;
- rerun tests and report precisely what remains unverified.

## Standard local-agent workflow

### 1. Inspect existing evidence first

Look for:

```text
~/.open-grocery-mcp/<store>/storage_state.json
local-captures/
captures/
diagnostics/http-contracts/
```

Use a previously saved session before asking for another login. Never display the contents of storage state.

### 2. Choose the least interactive capture path

Preferred order:

1. reuse an existing local storage state;
2. use the automated authenticated probe with credentials already supplied through local environment variables;
3. use a visible browser and browser automation;
4. ask the owner only to complete CAPTCHA, 2FA or password-manager unlock, then continue automatically.

For an automated visible probe:

```powershell
$env:OPEN_GROCERY_CAPTURE_HEADLESS = "0"
python .\tools\capture_http_contract.py `
  --store gadis `
  --mode authenticated `
  --output .\local-captures\gadis-authenticated.json
```

For Froiz, replace `gadis` with `froiz`.

For a browser-agent-driven capture, launch:

```powershell
python .\capture_http_local.py `
  --store gadis `
  --output .\local-captures\gadis-authenticated.json
```

The browser agent, not the owner, must select phases and perform the corresponding safe UI actions.

### 3. Validate observable results

Never infer success from a green button or a completed browser sequence. Validate the file:

```powershell
python .\tools\validate_capture.py `
  .\local-captures\gadis-authenticated.json `
  --minimum-events 5 `
  --require-response
```

For a full manual/browser-agent capture, also require the phases that were actually attempted, for example:

```powershell
python .\tools\validate_capture.py `
  .\local-captures\gadis-authenticated.json `
  --minimum-events 10 `
  --require-response `
  --require-phase cart_read `
  --require-phase cart_add `
  --require-phase cart_remove
```

A capture with `events: 0` is a failed capture even if every UI action appeared to work.

### 4. If events are zero, debug instead of asking the owner to repeat everything

Check, in this order:

1. the exact output path being validated;
2. `errors`, `timed_out`, `completed` and `phase_marks` in the JSON;
3. whether request listeners were attached before the first navigation;
4. whether listeners were attached to every newly opened page/popup;
5. whether the route handler raised an exception and silently prevented recording;
6. whether the browser was launched by the capture process rather than an unrelated Chrome window;
7. whether `should_record_request` filters out documents/XHR/fetch unexpectedly;
8. whether redirects or service workers move traffic to another page/context;
9. whether the selected browser channel behaves differently from bundled Chromium;
10. whether the agent validated an old file rather than the latest capture.

Patch the instrumentation, add a regression test, rerun the smallest safe flow and validate again. Do not tell the owner merely to redo the eleven phases.

### 5. Derive the HTTP contract

From the sanitized events, identify:

- method, host and path;
- required public context headers;
- authentication mechanism without retaining token values;
- request-body keys and primitive types;
- response schema and error statuses;
- cart versioning/idempotency requirements;
- address and delivery-slot dependencies;
- exact boundary between checkout creation and order submission.

Do not copy private identifier values into fixtures. Use placeholders and mocked responses.

### 6. Implement and test

The HTTP client must:

- fail closed when authentication or cart state is ambiguous;
- use prepare/confirm/commit for mutations;
- enforce maximum spend and expected cart version;
- reread the cart after every mutation;
- never place an order from an ordinary cart method;
- keep browser fallback available until the HTTP flow is verified locally.

Add unit tests for successful requests, expired sessions, changed cart versions, unavailable products, server errors, rollback attempts and blocked order/payment routes.

Run:

```powershell
pytest
python -m compileall -q src tests tools
ruff check .
```

### 7. Completion report

Report:

- commands actually run;
- capture path and validated event/request/response counts;
- phases and endpoints verified;
- files changed;
- tests and lint results;
- what is still unverified;
- explicit confirmation that no order/payment request reached the retailer.

Do not claim authenticated HTTP checkout is complete from public/guest traffic alone.

## Acceptance criteria for the current Gadis task

The task is not complete until the local agent has either:

1. produced a sanitized authenticated capture with non-zero request and response counts for cart read plus at least one reversible cart mutation, then implemented/tests the corresponding HTTP client; or
2. demonstrated with reproducible local evidence that a retailer control prevents the HTTP route, documented the exact blocker and left a working browser fallback.

The final order endpoint may remain unverified. It must remain disabled and blocked by default.
