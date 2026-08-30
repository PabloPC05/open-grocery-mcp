# MCP Tool Reference

## Overview

Open Grocery MCP provides two deployment modes with different tool sets:

1. **Public Hosted Instance** (`https://open-grocery-mcp.vercel.app/mcp`) - Read-only catalogue and comparison tools
2. **Local Authenticated Instance** (`stdio` or local HTTP) - Full authenticated retailer workflows

## Tool Availability by Deployment Mode

### ✅ Public Hosted Tools (No Authentication Required)

These tools are available on the public Vercel instance at `https://open-grocery-mcp.vercel.app/mcp`:

#### Catalogue & Search
- `health()` - Server version and capabilities
- `stores(country?)` - List supported stores and their capabilities
- `search_products(store, query, ...)` - Search one store catalogue
- `search_products_expanded(query, stores?, ...)` - Multi-store search with coverage diagnostics
- `get_product(store, product_id, ...)` - Get detailed product information
- `list_categories(store, ...)` - List product categories

#### Comparison & Analysis
- `compare_basket(basket, stores, ...)` - Compare basket across stores
- `compare_alternatives(items)` - Compare alternative products
- `search_offers(store, query, ...)` - Find promotional offers
- `filter_worthwhile_offers(...)` - Filter offers worth considering
- `optimize_basket_combination(...)` - Optimize basket across stores

#### Coverage & Delivery Info
- `get_delivery_coverage(store, postal_code)` - Public delivery fees and minimums

#### Local Drafts
- `prepare_cart(basket, store)` - Create local cart draft
- `get_cart_draft(draft_id)` - Read local draft
- `delete_cart_draft(draft_id)` - Delete local draft

#### Semantic Analysis
- `explain_product_equivalence(...)` - Explain product matching
- `explain_product_relationship(...)` - Explain product relationships
- `assess_substitution_candidate(...)` - Assess substitution quality
- `semantic_ontology_status()` - Ontology version and coverage

#### Quality Auditing
- `audit_semantic_corpus(...)` - Audit semantic quality
- `audit_catalogue_quality(...)` - Audit catalogue coverage

### 🔐 Local Authenticated Tools (Requires Local MCP)

These tools are **NOT available** on the public hosted instance. They require running Open Grocery MCP locally via `stdio` or local HTTP with appropriate environment variables:

#### Login & Session Management
- `account_status(store)` - Check if local session exists
- `login_with_browser(store, timeout_seconds?)` - Open browser for login/2FA
- `import_browser_session(store, storage_state_path)` - Import existing session
- `clear_session(store)` - Clear local session (logout)

#### Real Cart Operations
- `get_real_cart(store)` - Read authenticated retailer cart
- `prepare_real_cart_update(store, draft_id, max_total, ...)` - Preview cart update
- `prepare_clear_real_cart(store, ...)` - Preview clearing cart
- `commit_real_cart_update(confirmation_id, confirmation_phrase)` - Apply reviewed cart update

#### Delivery Addresses & Slots
- `list_delivery_addresses(store)` - List saved delivery addresses
- `get_delivery_slots(store, address_id)` - Get available delivery windows

#### Checkout
- `prepare_checkout_creation(store, max_total, ...)` - Preview checkout creation
- `commit_checkout_creation(confirmation_id, confirmation_phrase)` - Create checkout
- `get_checkout(store, checkout_id)` - Read checkout details
- `prepare_delivery_selection(store, checkout_id, address_id, slot_id, max_total)` - Preview delivery selection
- `commit_delivery_selection(confirmation_id, confirmation_phrase)` - Apply delivery selection

#### Human Handoff
- `prepare_human_handoff(store, max_total, ...)` - Revalidate final boundary
- `open_human_review(store, max_total, ...)` - Open browser for human review

#### Order Submission (Experimental, Disabled by Default)
- `prepare_order_submission(store, checkout_id, max_total)` - Preview order
- `submit_order(confirmation_id, confirmation_phrase, approval_code)` - Submit order

## Store Capabilities

Each store advertises its capabilities. Use `stores()` to see what each retailer supports:

### Common Capabilities

- `search` - Catalogue search
- `product` - Individual product details
- `categories` - Category browsing
- `compare` - Price comparison
- `draft_cart` - Local cart drafts
- `coverage` - Public delivery coverage info

### Authenticated Capabilities (Local Only)

- `login` - Browser-based login
- `account` - Account status checks
- `real_cart` - Real retailer cart access
- `cart_read` - Read cart without writes
- `cart_write` - Modify real cart (requires confirmation)
- `addresses` - Saved delivery addresses
- `slots` - Delivery time slots
- `delivery` - Full delivery selection
- `checkout` - Checkout creation and management
- `human_handoff` - Browser handoff for final review
- `order_submission_experimental` - Order placement (disabled by default)

## Store-Specific Notes

### Mercadona
- **Public**: Catalogue search requires postal code
- **Local**: Full HTTP workflow for cart, addresses, slots, checkout
- Checkout and order require HTTP+confirmation

### Gadis
- **Public**: Catalogue and coverage by postal code
- **Local**: HTTP for session, cart (whole units), addresses, slots; browser fallback for fractional quantities and address selection
- Checkout creation available via HTTP when delivery triple is provided

### Froiz
- **Public**: Authenticated catalogue preferred, falls back to public search
- **Local**: HTTP for cart, addresses, calendar; no separate checkout (orders/create places real order)
- Checkout and order submission unavailable by design

### Eroski
- **Public**: Public HTML/HTTP catalogue
- **Local**: HTTP cart reads, browser-verified writes; delivery GET-only for selected context
- Checkout and order submission unavailable by design

## Configuration

### Public Hosted Instance

No configuration needed. Connect directly:

```json
{
  "mcpServers": {
    "open-grocery-mcp": {
      "url": "https://open-grocery-mcp.vercel.app/mcp"
    }
  }
}
```

### Local Authenticated Instance

#### Stdio (Recommended)

```json
{
  "mcpServers": {
    "open-grocery-local": {
      "command": "open-grocery-mcp",
      "args": ["--allow-retailer-writes"],
      "env": {
        "OPEN_GROCERY_ENABLE_RETAILER_WRITES": "1"
      }
    }
  }
}
```

#### Local HTTP

```bash
open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --allow-retailer-writes
```

```json
{
  "mcpServers": {
    "open-grocery-local": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### Environment Variables for Local Instance

- `OPEN_GROCERY_ENABLE_RETAILER_WRITES=1` - Enable cart writes, address operations
- `OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1` - Enable order placement (experimental)
- `OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1` - Enable browser-driven orders
- `OPEN_GROCERY_ORDER_APPROVAL_CODE=<secret>` - Required for order submission (min 6 chars)

**⚠️ Security**: Never set retailer-write or order-submission variables on the public hosted instance.

## Workflow Recommendations

### Read-Only Research
Use the **public hosted instance** for:
- Product search and discovery
- Price comparison across stores
- Offer evaluation
- Delivery coverage checks
- Creating local cart drafts

### Authenticated Shopping
Use a **local authenticated instance** for:
1. Login via `login_with_browser(store)`
2. Check cart: `get_real_cart(store)`
3. Create draft: `prepare_cart(basket, store)`
4. Preview update: `prepare_real_cart_update(...)`
5. Commit: `commit_real_cart_update(...)`
6. List addresses: `list_delivery_addresses(store)`
7. Get slots: `get_delivery_slots(store, address_id)`
8. Preview checkout: `prepare_checkout_creation(...)`
9. Commit: `commit_checkout_creation(...)`
10. Human review: `open_human_review(...)` - Final action belongs to the user

## Safety & Confirmations

All authenticated writes use a two-phase pattern:

1. **Prepare** phase: Returns summary + one-use `confirmation_id` + exact `confirmation_phrase` + 5-minute expiry
2. **Commit** phase: Requires exact phrase, consumes confirmation_id once, re-verifies spending cap

Never infer confirmation. Always show the complete summary and exact phrase to the user before committing.

## Support

- Repository: https://github.com/PabloPC05/open-grocery-mcp
- Issues: Use GitHub issues for bugs and feature requests
- Security: See SECURITY.md for vulnerability reporting
