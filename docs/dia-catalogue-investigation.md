# Día España Public Catalogue Investigation

**Date:** 2026-08-30  
**Goal:** Add public read-only catalogue provider for Día Spain (dia.es)  
**Result:** ❌ No usable public catalogue found

## Executive Summary

After extensive investigation, **Día España does not provide a usable public catalogue API** that can be accessed without:
1. Establishing a session with location/postal code selection
2. Browser-based authentication
3. Client-side JavaScript execution with state management

This makes Día fundamentally different from other supported retailers (Mercadona, Eroski) that provide accessible public catalogues.

## Investigation Methods

### 1. HTTP Traffic Capture

Captured browser XHR/Fetch requests while:
- Loading homepage (`https://www.dia.es/`)
- Navigating to search (`https://www.dia.es/compra-online/search?text=leche`)
- Waiting for SPA hydration (8+ seconds)

**Observed API calls:**
- `GET /api/v2/home-insight/ad_placements` → 204 No Content
- `GET /api/v2/home-insight/initial_analytics` → 200 (analytics only, NO products)
- `POST /2jKappxi/...` → 201 (bot detection/fingerprinting)

**NOT observed:**
- No product search requests
- No product list requests
- No catalogue API calls

### 2. HTML Analysis

The search page HTML:
- Redirects `compra-online/search?text=X` → `/?text=X` (homepage)
- Contains **zero product data** in server-rendered HTML
- Shows home page title: "Supermercado online | ¡Recibe tu compra hoy mismo! | Día"
- Mentions "código postal" and "dirección de entrega" requirements
- No `<product>` elements, no product cards in DOM after JavaScript execution

### 3. API Endpoint Discovery

Found these internal API paths in source code:
- `/api/v1/cart`
- `/api/v1/cart-insight`
- `/api/v1/common-aggregator`
- `/api/v1/customer-legal/legal-conditions`
- `/api/v1/list-back`
- `/api/v1/search-back` ← Primary search candidate
- `/api/v1/search-insight`
- `/api/v2/home-back`
- `/api/v2/home-insight`

### 4. Endpoint Testing

All endpoints returned **404 Not Found**:

**GET requests tried:**
- `/api/v1/search-back?q=leche`
- `/api/v1/search-back?searchText=leche`
- `/api/v1/search-back?text=leche`
- `/api/v1/search-insight?q=leche`
- `/api/v2/search?q=leche`
- `/api/v2/products/search?q=leche`

**POST requests tried:**
- `POST /api/v1/search-back` with JSON payloads:
  - `{"q": "leche"}`
  - `{"searchText": "leche"}`
  - `{"text": "leche"}`
  - `{"query": "leche"}`
  - `{"search": "leche", "limit": 10}`

**Headers used (matching browser):**
- `cart_id`, `session_id`, `customer_id`, `customer_code`
- `x-locale: es`, `x-mobile`, `x-requested-with: XMLHttpRequest`
- `User-Agent`, `Accept`, `Referer`, `Origin`

**Cookies tried:**
- `postal_code=28001`
- `delivery_postal_code=28001`
- `location=28001`

## Technical Barriers

### 1. Location Gating
Día requires a valid Spanish postal code **before** showing any products. This appears to be enforced at the application/session level, not just as a URL parameter.

### 2. SPA Architecture
The site is a heavy Single Page Application that:
- Loads an empty shell on initial request
- Requires JavaScript execution to fetch products
- Manages state client-side (React/Next.js based on `__NEXT_DATA__` presence)
- Makes API calls only after establishing session context

### 3. Session Requirements
Product APIs likely require:
- Valid session established through UI interaction
- Postal code selected via proper flow
- Possibly cart initialization
- State stored in browser storage (localStorage/sessionStorage)

## Comparison with Other Providers

| Provider | Public Catalogue | Location Required | Works on Vercel | Implementation |
|----------|-----------------|-------------------|-----------------|----------------|
| **Mercadona** | ✅ Yes | Optional (postal code param) | ✅ Yes | JSON API |
| **Eroski** | ✅ Yes | Validated but works | ✅ Yes (with cookie fallback) | HTML scraping |
| **Gadis** | ✅ Yes | Context headers | ✅ Yes | JSON API |
| **Froiz** | ✅ Yes | Context headers | ✅ Yes | JSON API |
| **Día** | ❌ **No** | ✅ **Required** | ❌ **No** | N/A |

## Why This Blocks Implementation

A "public read-only catalogue provider" must:
1. ✅ Work without authentication
2. ✅ Be callable from serverless (Vercel Lambda)
3. ✅ Not require browser automation
4. ✅ Have discoverable HTTP endpoints

Día fails requirements **1, 2, and 3**.

Attempting to implement a provider would require:
- **Browser automation** (Playwright/Puppeteer) to set postal code
- **Persistent session** across requests
- **Local or stateful environment** (not Vercel Lambda)
- **Reverse engineering** of undocumented private APIs

This matches the scope of **authenticated providers** (Gadis/Froiz checkout flows), not public catalogue providers.

## Recommendations

### Option 1: Do Not Implement (Recommended)
- **Status:** Día does not meet "public catalogue" criteria
- **Action:** Document in this PR that Día is not viable for public read-only access
- **Future:** Revisit when implementing authenticated/session-based providers

### Option 2: Implement with Browser (Out of Scope)
- Requires browser automation to establish location
- Similar to authenticated Gadis/Froiz flows
- Does not work on hosted Vercel instance
- Contradicts "public read-only" requirement

### Option 3: Wait for Official API
- Monitor https://www.dia.es for API documentation
- Check if Día provides partner/developer API access
- Unlikely for grocery catalogue

## Evidence Files

Investigation artifacts saved to `local-captures/`:
- `dia-traffic.json` - Initial XHR capture
- `dia-search-api.json` - Filtered API calls
- `dia-full-traffic.json` - Complete traffic log (195 events)
- `dia-search-page.html` - Rendered HTML (380KB, no products)

## Conclusion

**Día España cannot be added as a public catalogue provider** in this PR without violating the project's architecture principles:
1. No fake/non-functional providers on production
2. Public providers must work without browser/session
3. Do not guess or scrape behind authentication walls

Per instructions: *"If Día has no usable public catalogue, stop and document evidence in the PR — do not ship a fake provider."*

This investigation confirms **Día has no usable public catalogue** for the requested integration pattern.
