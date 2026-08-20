# Open Grocery MCP

Servidor [Model Context Protocol](https://modelcontextprotocol.io/) para buscar productos, comparar una misma cesta entre supermercados y gestionar, con confirmaciones explícitas, el carrito y el checkout de la cuenta del usuario.

> **Alpha `0.3.0`.** Gadis, Froiz y Mercadona ofrecen catálogo/comparación y un flujo autenticado de carrito, entrega y checkout. Mercadona usa su API autenticada; Gadis y Froiz usan la interfaz web real mediante Playwright para no depender de rutas privadas inventadas. El envío definitivo permanece apagado por defecto y ninguna integración se presenta como transacción real validada hasta hacer deliberadamente una compra de prueba.

## Estado actual

| Supermercado | Catálogo | Comparar | Carrito real | Entrega/checkout | Pedido final |
|---|---:|---:|---:|---:|---:|
| Gadis | Sí | Sí | Sí, navegador | Sí, navegador | Experimental y apagado |
| Froiz | Sí | Sí | Sí, navegador | Sí, navegador | Experimental y apagado |
| Mercadona | Sí | Sí | Sí, API | Sí, API | Experimental y apagado |

La automatización de Gadis y Froiz opera sobre botones, campos y opciones visibles en una sesión local del usuario. También escucha respuestas JSON para leer mejor el carrito cuando la web las expone, pero no codifica endpoints privados de escritura.

## Herramientas MCP

Catálogo y comparación:

- `health`, `stores`
- `search_products`, `get_product`, `list_categories`
- `compare_basket`
- `prepare_cart`, `get_cart_draft`, `delete_cart_draft`

Cuenta y compra, disponibles en las tres cadenas:

- `account_status`, `login_with_browser`, `import_browser_session`
- `get_real_cart`
- `prepare_real_cart_update`, `prepare_clear_real_cart`, `commit_real_cart_update`
- `list_delivery_addresses`, `get_delivery_slots`
- `prepare_checkout_creation`, `commit_checkout_creation`, `get_checkout`
- `prepare_delivery_selection`, `commit_delivery_selection`
- `prepare_order_submission`, `submit_order`

Las herramientas `prepare_*` no ejecutan la operación descrita. Devuelven un resumen, un `confirmation_id`, una frase exacta y una caducidad de cinco minutos. El `commit_*` correspondiente exige la frase exacta y consume el identificador una sola vez.

## Protecciones de compra

- Las escrituras están deshabilitadas salvo `OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`.
- Cada cambio usa un plan revisado, límite máximo de gasto y comprobación del carrito inmediatamente anterior.
- Tras escribir se vuelve a leer el carrito y se comprueban productos y cantidades.
- Si el total no puede verificarse, se falla de forma cerrada y se intenta restaurar la cesta anterior.
- Las direcciones se devuelven redactadas; tokens, cookies y contraseñas nunca son parámetros MCP.
- Productos de alcohol, tabaco, vapeo o nicotina no se añaden automáticamente.
- Checkout y pedido son acciones separadas.
- Un intento de pedido se registra antes del clic final y nunca se reintenta automáticamente; si el resultado es ambiguo, hay que revisar el historial de pedidos de la tienda.
- No se automatizan PSD2, 3-D Secure, códigos SMS, biometría ni confirmaciones bancarias.

El pedido final requiere simultáneamente:

```text
OPEN_GROCERY_ENABLE_RETAILER_WRITES=1
OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1
OPEN_GROCERY_ORDER_APPROVAL_CODE=<secreto local de al menos 6 caracteres>
```

Gadis y Froiz requieren además:

```text
OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1
```

Ejemplos de frases de un solo uso:

```text
CONFIRMAR CARRITO 52.38 EUR
COMPRAR 52.38 EUR
```

## Instalación

Requiere Python 3.11 o posterior.

```bash
git clone https://github.com/PabloPC05/open-grocery-mcp.git
cd open-grocery-mcp
git switch feat/initial-mcp
python -m venv .venv
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -e ".[dev,browser]"
playwright install chromium
pytest
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
playwright install chromium
pytest
```

También puede utilizarse Google Chrome instalado:

```text
OPEN_GROCERY_BROWSER_CHANNEL=chrome
```

O indicar un ejecutable:

```text
OPEN_GROCERY_BROWSER_EXECUTABLE=/ruta/a/chrome
```

## Sesiones locales

`login_with_browser(store="gadis")`, `login_with_browser(store="froiz")` o `login_with_browser(store="mercadona")` abre un navegador visible. El usuario inicia sesión directamente en la web y completa cualquier verificación. Para Gadis/Froiz pulsa después el botón negro **Open Grocery: guardar sesión**.

Por defecto, las sesiones de navegador se guardan en:

```text
~/.open-grocery-mcp/gadis/storage_state.json
~/.open-grocery-mcp/froiz/storage_state.json
~/.open-grocery-mcp/mercadona/storage_state.json
```

Puede cambiarse la raíz de Gadis/Froiz mediante `OPEN_GROCERY_STATE_DIR`. Estos archivos equivalen a una sesión iniciada: no deben compartirse ni subirse a Git.

## Ejecución

Solo catálogo y borradores locales:

```bash
open-grocery-mcp
```

Permitir modificaciones confirmadas de carrito y checkout:

```bash
open-grocery-mcp --allow-retailer-writes
```

Permitir el endpoint final de Mercadona:

```bash
export OPEN_GROCERY_ORDER_APPROVAL_CODE='un-codigo-local-largo'
open-grocery-mcp --allow-retailer-writes --allow-order-submission
```

Permitir también el botón final de Gadis/Froiz:

```bash
export OPEN_GROCERY_ORDER_APPROVAL_CODE='un-codigo-local-largo'
open-grocery-mcp \
  --allow-retailer-writes \
  --allow-order-submission \
  --allow-browser-order-submission
```

Configuración genérica de cliente MCP:

```json
{
  "mcpServers": {
    "open-grocery": {
      "command": "/ruta/al/entorno/bin/open-grocery-mcp",
      "env": {
        "OPEN_GROCERY_ENABLE_RETAILER_WRITES": "1",
        "OPEN_GROCERY_BROWSER_CHANNEL": "chrome"
      }
    }
  }
}
```

## Flujo recomendado

1. `compare_basket` compara la lista.
2. `prepare_cart` crea un borrador local de la tienda elegida.
3. `login_with_browser` guarda una sesión local.
4. `get_real_cart` devuelve versión y total.
5. `prepare_real_cart_update` produce un plan y una frase de confirmación.
6. `commit_real_cart_update` aplica exactamente ese plan.
7. `prepare_checkout_creation` y `commit_checkout_creation` abren un checkout revisado.
8. Se consultan direcciones y franjas; en los proveedores de navegador, las franjas se vinculan a ese checkout confirmado.
9. La entrega sigue preparar → confirmar → aplicar.
10. El pedido final se prepara y confirma por separado.

## Comparación de cesta

```json
{
  "postal_code": "28050",
  "stores": ["gadis", "froiz", "mercadona"],
  "items": [
    {"query": "leche entera 1 L", "quantity": 2},
    {"query": "huevos camperos 12 unidades", "quantity": 1},
    {"query": "arroz redondo 1 kg", "quantity": 1, "max_unit_price": 2.5}
  ]
}
```

Los resultados pueden no ser SKU idénticos. Deben revisarse las coincidencias de baja confianza. En Gadis/Froiz el surtido efectivo de compra es el seleccionado por la sesión del navegador; el índice público de catálogo puede no coincidir exactamente con una zona concreta.

## Transporte HTTP

```bash
open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

No expongas esta alpha directamente a Internet. No incorpora identidad multiusuario ni aislamiento de sesiones. Los flujos con navegador están pensados principalmente para `stdio` en la máquina del propietario.

## Desarrollo y verificación

```bash
pytest
python -m compileall -q src tests
ruff check .
```

Las pruebas automatizadas usan transportes y navegadores simulados. Cubren normalización del DOM, aislamiento de sesión, límites de gasto, control de versión, rollback, URLs privadas de checkout, confirmaciones de un uso y bloqueo del pedido. No realizan compras reales.

La implementación de Gadis/Froiz está completa como backend de navegador, pero sus selectores adaptativos deben validarse la primera vez contra una sesión real porque la interfaz de una tienda puede cambiar sin aviso. Un fallo de selector detiene la operación; no se interpreta como éxito.

Consulta [la arquitectura autenticada](docs/authenticated-workflows.md), [los proveedores de navegador](docs/browser-providers.md), [el contrato de proveedores](docs/provider-contract.md) y [la política de seguridad](SECURITY.md).

## Licencia y procedencia

MIT. Algunas integraciones se apoyan en conocimiento y código de proyectos MIT anteriores; los avisos completos están en [`NOTICE`](NOTICE). Las integraciones no son oficiales y las marcas pertenecen a sus titulares.
