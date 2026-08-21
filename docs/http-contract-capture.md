# HTTP contract capture for Gadis and Froiz

This diagnostic is the migration path from Playwright-driven shopping to the lighter Mercadona-style architecture:

```text
visible browser login -> sanitized request capture -> retailer-specific HTTP client
```

It never clicks a final order button. Potential order and payment requests are also blocked at the browser routing layer.

## What it inspects

1. Storefront bootstrap and location/session calls.
2. Login request shape when authenticated mode is enabled.
3. Add-to-cart.
4. Cart read.
5. Quantity `1 -> 2 -> 1`.
6. Checkout navigation and visible address/slot controls.
7. Cleanup by removing the diagnostic product.

The probe chooses a low-cost non-restricted grocery item from the public catalogue. Alcohol, tobacco, vape and nicotine products are excluded.

## Sanitization

The generated artifact redacts or removes:

- passwords and secrets;
- `Authorization`, cookie and CSRF/XSRF values;
- access, refresh and session tokens;
- email addresses and phone numbers;
- customer, user and account identifiers;
- street addresses and postal codes;
- payment, card and bank fields;
- URL fragments and sensitive query parameters.

The raw HAR is deleted immediately after sanitized JSON is written. Never upload a raw HAR, browser storage state or screenshots of private account pages.

## Guest capture

The committed [`tools/capture-request.json`](../tools/capture-request.json) initially requests:

```json
{"store": "all", "mode": "guest"}
```

Changing that file and pushing it to `feat/initial-mcp` triggers the capture workflow.

## Authenticated capture

Do not paste credentials into source code, issues, pull requests or chat. Create a disposable retailer account and store the values as GitHub Actions repository secrets:

```text
GADIS_TEST_USERNAME
GADIS_TEST_PASSWORD
FROIZ_TEST_USERNAME
FROIZ_TEST_PASSWORD
```

While the workflow exists only on `feat/initial-mcp`, authenticated capture is triggered by changing [`tools/capture-request.json`](../tools/capture-request.json) to:

```json
{"store": "all", "mode": "authenticated"}
```

and pushing that change. After the workflow is merged into the default branch, it can also be started manually from the Actions tab through `workflow_dispatch`.

The disposable account should have:

- no saved payment method;
- no active orders;
- no loyalty balance or coupons of value;
- a unique password not used anywhere else.

A delivery address is optional for cart discovery. Add one only when it is fictitious but accepted by the retailer or when the tester is authorized to use it. The probe does not create an address and does not submit an order.

## Turning an artifact into an HTTP provider

For each retailer:

1. Identify session cookies, CSRF headers and refresh/bootstrap calls.
2. Map cart reads and add/set/remove request shapes.
3. Determine site/store/location identifiers and concurrency/version semantics.
4. Map addresses, slots and checkout creation.
5. Implement fixture tests before enabling live writes.
6. Retain Playwright only for login, CAPTCHA or anti-bot cookie renewal when required.
7. Keep the final order endpoint disabled until a deliberate real transaction validates it.

Sanitized artifacts are retained for 14 days by GitHub Actions. Review and download them only from the repository's Actions page.
