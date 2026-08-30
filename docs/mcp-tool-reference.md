# MCP Tool Reference

Referencia completa de las herramientas Model Context Protocol disponibles en Open Grocery MCP.

## Índice

- [Shopping UX](#shopping-ux)
  - [Prepare Purchase](#prepare-purchase)
  - [Shopping Lists](#shopping-lists)
  - [Shared Addresses](#shared-addresses)
  - [Shopping Profile](#shopping-profile)
  - [Delivery Intent](#delivery-intent)
- [Catálogo y búsqueda](#catálogo-y-búsqueda)
- [Comparación y optimización](#comparación-y-optimización)
- [Cuenta autenticada](#cuenta-autenticada)
- [Carrito autenticado](#carrito-autenticado)
- [Entrega y checkout](#entrega-y-checkout)

---

## Shopping UX

### Prepare Purchase

#### `prepare_purchase`

Herramienta principal de compra que une todo el flujo de UX.

**Parámetros:**
- `items` (opcional): Lista de items ad-hoc (strings o dicts con `query` y `quantity`)
- `list_id` (opcional): ID de lista de compra para usar en lugar de items
- `store` (opcional): Tienda preferida. Si no se proporciona, compara todas.
- `postal_code` (opcional): Código postal. Si no se proporciona, usa la dirección por defecto.
- `max_total` (opcional): Total máximo de gasto. Si no se proporciona, usa el perfil.
- `search_limit` (int, default 10): Número de resultados de búsqueda por item
- `eco` (bool, default false): Preferir productos eco
- `include_loyalty` (bool, default false): Incluir precios de fidelización
- `multi_store` (bool, default false): Permitir dividir entre múltiples tiendas

**Retorna:**
- `strategy`: "single_store", "comparison", o "multi_store"
- `store` o `recommended_store`: Tienda recomendada
- `draft_id` o `draft_ids`: IDs de borradores creados
- `basket_result` o `comparison_result`: Detalles completos de productos y totales
- `exceeds_max_total`: True si excede el presupuesto
- `profile_applied`: Configuración del perfil aplicada

**Ejemplo:**
```python
prepare_purchase(
    list_id="habitual",
    postal_code="28001",
    max_total=50.0,
)
```

---

### Shopping Lists

#### `create_shopping_list`

Crea una lista de compra nueva.

**Parámetros:**
- `name` (requerido): Nombre de la lista
- `list_id` (opcional): ID específico para la lista

**Retorna:**
- `list_id`: ID generado o especificado
- `name`: Nombre de la lista
- `items`: Array vacío inicialmente
- `created_at`, `updated_at`: Timestamps ISO 8601

#### `list_shopping_lists`

Lista todas las listas de compra con sus conteos de items.

**Retorna:**
Lista de objetos con:
- `list_id`, `name`, `item_count`, `created_at`, `updated_at`

#### `get_shopping_list`

Obtiene una lista de compra completa con todos sus items.

**Parámetros:**
- `list_id` (requerido): ID de la lista

**Retorna:**
Objeto completo de la lista con array `items`.

#### `add_list_item`

Añade un item a una lista de compra.

**Parámetros:**
- `list_id` (requerido): ID de la lista
- `item` (requerido): Nombre del producto
- `quantity` (float, default 1.0): Cantidad
- `notes` (opcional): Notas adicionales

**Retorna:**
- `list_id`: ID de la lista
- `item`: Objeto del item añadido

#### `update_list_item`

Actualiza un item existente en una lista.

**Parámetros:**
- `list_id` (requerido): ID de la lista
- `item_index` (requerido): Índice del item (0-based)
- `item` (opcional): Nuevo nombre
- `quantity` (opcional): Nueva cantidad
- `notes` (opcional): Nuevas notas

**Retorna:**
- `list_id`, `item_index`, `item`: Item actualizado

#### `remove_list_item`

Elimina un item de una lista.

**Parámetros:**
- `list_id` (requerido): ID de la lista
- `item_index` (requerido): Índice del item

**Retorna:**
- `list_id`, `item_index`, `removed`: Item eliminado

#### `delete_shopping_list`

Elimina una lista de compra completa.

**Parámetros:**
- `list_id` (requerido): ID de la lista

**Retorna:**
- `list_id`, `deleted`: true/false

#### `store_last_basket`

Almacena el último resultado de basket para replay.

**Parámetros:**
- `basket_result` (requerido): Resultado completo de basket

**Retorna:**
- `status`: "stored"
- `stored_at`: Timestamp ISO 8601

#### `replay_last_basket`

Recupera el último basket almacenado.

**Retorna:**
- `stored_at`: Cuándo se almacenó
- `basket`: Resultado completo del basket

O `{"status": "no_basket_stored"}` si no hay ninguno.

---

### Shared Addresses

#### `add_postal_address`

Añade una dirección al libro compartido.

**Parámetros:**
- `postal_code` (requerido): Código postal (mín. 4 caracteres)
- `label` (opcional): Etiqueta descriptiva (ej: "Casa", "Trabajo")
- `street` (opcional): Calle
- `city` (opcional): Ciudad
- `set_as_default` (bool, default false): Marcar como dirección por defecto

**Retorna:**
- `address`: Objeto de dirección con `id` generado
- `is_default`: true/false

#### `list_shared_addresses`

Lista todas las direcciones compartidas.

**Retorna:**
- `addresses`: Array de direcciones
- `default_address_id`: ID de la dirección por defecto

#### `get_default_postal_address`

Obtiene la dirección por defecto.

**Retorna:**
Objeto de dirección o `{"status": "no_default_set"}`.

#### `set_default_address`

Marca una dirección como por defecto.

**Parámetros:**
- `address_id` (requerido): ID de la dirección

**Retorna:**
- `default_address_id`: ID ahora por defecto

#### `remove_postal_address`

Elimina una dirección del libro compartido.

**Parámetros:**
- `address_id` (requerido): ID de la dirección

**Retorna:**
- `address_id`, `removed`: true/false

#### `set_default_postal_code`

**Nueva herramienta simplificada** para establecer un código postal predeterminado sin necesidad de proporcionar una dirección completa.

**Parámetros:**
- `postal_code` (requerido): Código postal español (5 dígitos, ej: "15001", "28001")
- `city` (opcional): Nombre de la ciudad
- `label` (opcional): Etiqueta descriptiva (por defecto: "Default")

**Retorna:**
- `address`: Objeto de dirección creada/actualizada con `id`
- `is_default`: true
- `message`: Confirmación

**Validación:**
- El código postal debe ser exactamente 5 dígitos numéricos
- Se valida el formato español automáticamente

**Nota:**
En instalaciones locales, la dirección persiste en `~/.open-grocery-mcp/shared_addresses.json`. En instancias hosted/Vercel, el almacenamiento es efímero; configure `OPEN_GROCERY_DEFAULT_POSTAL_CODE` como variable de entorno para un valor predeterminado de instancia persistente. Si no se configura ningún valor, se usa el código postal de fábrica `15702` (Santiago de Compostela, España).

#### `get_default_postal_code`

Obtiene el código postal predeterminado actual y su origen.

**Parámetros:**
Ninguno

**Retorna:**
- `postal_code`: Código postal predeterminado (nunca null; siempre devuelve al menos el código de fábrica `15702`)
- `source`: Origen del código postal:
  - `"shared_default"`: Desde dirección compartida predeterminada
  - `"env"`: Desde variable de entorno `OPEN_GROCERY_DEFAULT_POSTAL_CODE`
  - `"builtin"`: Código postal de fábrica `15702` (Santiago de Compostela, España)
- `address` (opcional): Objeto de dirección completa si `source` es `"shared_default"`

**Orden de resolución:**
1. Dirección compartida predeterminada (establecida vía `set_default_postal_code()` o `add_postal_address()`)
2. Variable de entorno `OPEN_GROCERY_DEFAULT_POSTAL_CODE`
3. Código postal de fábrica: `15702` (Santiago de Compostela, España)

#### `map_shared_address_to_retailer`

Mapea una dirección compartida a una dirección de retailer.

**Parámetros:**
- `store` (requerido): Nombre de la tienda
- `address_id` (opcional): ID de dirección compartida. Si no se proporciona, usa la por defecto.

**Retorna:**
- `matched`: true si se encontró coincidencia
- `matched_address`: Dirección del retailer (si matched=true)
- `needs_creation`: true si hay que crear la dirección
- `can_create_http`: true si HTTP creation está disponible
- `guidance`: Instrucciones si se requiere creación manual

---

### Shopping Profile

#### `get_shopping_profile`

Obtiene el perfil de compra actual.

**Retorna:**
- `default_max_total`: Presupuesto máximo por defecto
- `excluded_terms`: Términos excluidos de productos
- `allergies`: Lista de alergias
- `private_label_preference`: "any", "prefer", "only", "never"
- `include_loyalty_default`: true/false
- `substitution_policy`: "allow", "prefer_brand", "never"
- `preferred_brands`: Lista de marcas preferidas

#### `update_shopping_profile`

Actualiza el perfil de compra. Todos los parámetros son opcionales.

**Parámetros:**
- `default_max_total` (float, opcional): Presupuesto máximo
- `excluded_terms` (list, opcional): Términos a excluir
- `allergies` (list, opcional): Lista de alergias
- `private_label_preference` (str, opcional): "any", "prefer", "only", "never"
- `include_loyalty_default` (bool, opcional): Incluir precios de fidelización por defecto
- `substitution_policy` (str, opcional): "allow", "prefer_brand", "never"
- `preferred_brands` (list, opcional): Marcas preferidas

**Retorna:**
Perfil completo actualizado.

#### `reset_shopping_profile`

Restablece el perfil a valores por defecto.

**Retorna:**
Perfil por defecto.

---

### Delivery Intent

#### `resolve_delivery_slot_intent`

Resuelve una franja de entrega por intención en lenguaje natural.

**Parámetros:**
- `store` (requerido): Nombre de la tienda
- `address_id` (requerido): ID de dirección de entrega del retailer
- `intent` (requerido): Intención en lenguaje natural

**Intenciones soportadas:**
- `"next_available"`, `"próximo"`, `"primero"`: Primera franja disponible
- `"today"`, `"hoy"`: Hoy (la más temprana)
- `"tomorrow"`, `"mañana"`: Mañana
- Día de semana: `"monday"`, `"lunes"`, `"tuesday"`, `"martes"`, etc.
- Franja horaria: `"morning"`, `"afternoon"`, `"tarde"`, `"evening"`, `"noche"`
- Fecha específica: `"2026-09-05"` o `"05/09/2026"`

**Retorna:**
- Si matched=true:
  - `matched`: true
  - `intent`: Intención reconocida
  - `slot`: Objeto de slot con `id`, `date`, `start`, `end`
- Si matched=false:
  - `matched`: false
  - `reason`: Razón del fallo
  - `nearest_options`: Array de slots más cercanos disponibles

**Ejemplo:**
```python
resolve_delivery_slot_intent(
    store="gadis",
    address_id="addr_123",
    intent="sábado mañana",
)
```

---

## Catálogo y búsqueda

### Resolución automática de códigos postales

Todas las herramientas de catálogo, búsqueda, comparación y cobertura resuelven automáticamente el código postal cuando se omite el parámetro `postal_code`. El orden de prioridad es:

1. **Argumento explícito** — el `postal_code` pasado a la herramienta (siempre gana)
2. **Dirección compartida predeterminada** — establecida vía `set_default_postal_code()` o `add_postal_address()`
3. **Variable de entorno** — `OPEN_GROCERY_DEFAULT_POSTAL_CODE`
4. **Código postal de fábrica** — `15702` (Santiago de Compostela, España) como última opción de respaldo

Las herramientas que resuelven el código postal incluyen `postal_code_source` en su respuesta (`"argument"`, `"shared_default"`, `"env"` o `"builtin"`) para que el cliente sepa qué valor se usó. La fuente `"builtin"` indica que se está usando el código postal de fábrica.

**Herramientas afectadas:**
- `search_products`, `search_products_expanded`
- `get_delivery_coverage` (ahora postal_code es opcional)
- `search_offers`, `filter_worthwhile_offers`
- `get_product`, `list_categories`
- `compare_basket`, `compare_alternatives`, `optimize_basket_combination`
- `audit_catalogue_quality`
- `prepare_cart`, `prepare_purchase`

### `health`

Retorna versión del servidor, modo de seguridad y tiendas registradas.

### `stores`

Lista todas las tiendas disponibles con sus capacidades.

### `search_products`

Busca productos en una tienda.

**Parámetros:**
- `store` (requerido): Nombre de la tienda
- `query` (requerido): Término de búsqueda
- `limit` (int, 1-100): Número de resultados
- `postal_code` (opcional): Código postal para localizar
- `eco` (bool): Preferir productos eco
- `include_loyalty` (bool): Incluir precios de fidelización

### `search_products_expanded`

Búsqueda expandida con aliases y grupos de términos requeridos.

**Parámetros:**
Como `search_products` más:
- `required_term_groups` (opcional): Array de arrays de términos alternativos

### `get_product`

Obtiene detalles de un producto específico por ID.

### `list_categories`

Lista categorías disponibles en una tienda.

---

## Comparación y optimización

### `compare_basket`

Compara una cesta en múltiples tiendas.

**Parámetros:**
- `items` (requerido): Lista de items (strings o dicts)
- `stores` (opcional): Lista de tiendas a comparar
- `postal_code` (opcional): Para calcular entrega
- `search_limit`, `eco`, `include_loyalty`

**Retorna:**
Comparación de tiendas con totales, entrega y mínimos.

### `compare_alternatives`

Compara alternativas de productos similares con precios normalizados.

### `optimize_basket_combination`

Optimiza una cesta dividiéndola entre múltiples tiendas.

**Parámetros:**
Como `compare_basket` más restricciones semánticas.

### `prepare_cart`

Crea un borrador de carrito local. No toca el retailer.

**Parámetros:**
- `store` (requerido): Nombre de la tienda
- `items` (requerido): Lista de items
- `postal_code`, `search_limit`, `eco`

**Retorna:**
Objeto de draft con `draft_id`.

### `get_cart_draft`

Lee un borrador de carrito por ID.

### `delete_cart_draft`

Elimina un borrador de carrito.

---

## Cuenta autenticada

### `account_status`

Verifica si existe una sesión local para una tienda.

### `login_mercadona`

Abre un navegador visible para iniciar sesión en Mercadona.

**Parámetros:**
- `timeout_seconds` (int, default 300): Timeout

### `login_gadis`

Abre un navegador visible para iniciar sesión en Gadis.

**Parámetros:**
- `timeout_seconds` (int, default 300): Timeout

### `login_froiz`

Abre un navegador visible para iniciar sesión en Froiz.

**Parámetros:**
- `timeout_seconds` (int, default 300): Timeout

### `login_dia`

Abre un navegador visible para iniciar sesión en Día (guarda la sesión para evitar bloqueo anti-bot).

**Parámetros:**
- `timeout_seconds` (int, default 300): Timeout

### `login_eroski`

Abre un navegador visible para iniciar sesión en Eroski.

**Parámetros:**
- `timeout_seconds` (int, default 300): Timeout

### `login_with_browser`

Abre un navegador visible para que el usuario inicie sesión (método genérico).

**Parámetros:**
- `store` (requerido): Nombre de la tienda
- `timeout_seconds` (int, default 300): Timeout

### `import_browser_session`

Importa un archivo `storage_state.json` por ruta local.

### `clear_session`

Limpia una sesión local (logout).

**Parámetros:**
- `store` (requerido): Nombre de la tienda

---

## Carrito autenticado

### `get_real_cart`

Lee el carrito real del retailer sin modificarlo.

### `prepare_real_cart_update`

Previsualiza aplicar un draft al carrito real. NO escribe.

**Parámetros:**
- `store` (requerido): Nombre de la tienda
- `draft_id` (requerido): ID del draft
- `max_total` (requerido): Total máximo permitido
- `expected_cart_version` (opcional): Versión esperada del carrito
- `mode` (str, default "merge"): "merge" o "replace"

**Retorna:**
- `confirmation_id`: ID para commit
- `confirmation_phrase`: Frase exacta requerida
- `summary`: Resumen de cambios

### `prepare_clear_real_cart`

Previsualiza vaciar el carrito real.

### `commit_real_cart_update`

Aplica una actualización previamente preparada.

**Parámetros:**
- `confirmation_id` (requerido): ID del prepare
- `confirmation_phrase` (requerido): Frase exacta

---

## Entrega y checkout

### `list_delivery_addresses`

Lista direcciones de entrega del retailer (redactadas).

### `get_delivery_slots`

Lista franjas de entrega disponibles para una dirección.

**Parámetros:**
- `store` (requerido): Nombre de la tienda
- `address_id` (requerido): ID de dirección del retailer

### `prepare_checkout_creation`

Previsualiza crear un checkout del carrito actual.

### `commit_checkout_creation`

Crea el checkout tras revisión.

### `get_checkout`

Lee el estado actual de un checkout.

### `prepare_human_handoff`

Revalida la última frontera segura antes de handoff.

### `open_human_review`

Abre navegador en la pantalla más avanzada segura para revisión humana.

---

## Notas de uso

1. **Flujo prepare/commit**: Todas las escrituras de retailer siguen el patrón prepare → revisar → commit con frase exacta.
2. **Confirmations expiran en 5 minutos**: Los `confirmation_id` son de un solo uso.
3. **Datos locales persistentes**: Listas, direcciones y perfil se guardan en `~/.open-grocery-mcp/`.
4. **Sesiones autenticadas**: `storage_state.json` nunca debe compartirse o subirse a Git.
5. **Order submission deshabilitado por defecto**: Requiere múltiples flags de entorno.
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
- **Public**: Public HTML/HTTP catalogue (often blocked by anti-bot protection from datacenter/serverless IPs)
- **Local**: HTTP cart reads, browser-verified writes; delivery GET-only for selected context
- Checkout and order submission unavailable by design
- **Note**: Public catalogue search may return explicit anti-bot error from hosted MCP. Local MCP with saved browser session (`~/.open-grocery-mcp/eroski/storage_state.json`) can retry with official cookies, often bypassing the challenge

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
