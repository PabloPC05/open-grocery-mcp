# Open Grocery MCP

Servidor [Model Context Protocol](https://modelcontextprotocol.io/) para buscar productos, comparar una cesta entre supermercados y gestionar, con confirmaciones explícitas, el carrito y el checkout de la cuenta del usuario.

> **Alpha `0.4.0`.** Mercadona usa su API autenticada. Gadis y Froiz mantienen un backend de navegador para las operaciones de cuenta, pero el proyecto ya incluye una captura HTTP redactada para migrarlos progresivamente al mismo modelo eficiente de Mercadona.

## Estado actual

| Supermercado | Catálogo | Comparar | Carrito/checkout | HTTP autenticado |
|---|---:|---:|---:|---:|
| Gadis | Sí, por código postal | Sí, con portes y mínimos públicos | Sí, navegador | En investigación mediante captura |
| Froiz | Sí | Sí | Sí, navegador | En investigación mediante captura |
| Mercadona | Sí, por código postal | Sí | Sí | Sí |

El envío definitivo permanece experimental y apagado por defecto. Ninguna integración se presenta como transacción real validada hasta hacer deliberadamente una compra de prueba.

## Herramientas MCP

Catálogo y comparación:

- `health`, `stores`
- `get_delivery_coverage`
- `search_products`, `get_product`, `list_categories`
- `compare_basket`
- `prepare_cart`, `get_cart_draft`, `delete_cart_draft`

Cuenta y compra:

- `account_status`, `login_with_browser`, `import_browser_session`
- `get_real_cart`
- `prepare_real_cart_update`, `prepare_clear_real_cart`, `commit_real_cart_update`
- `list_delivery_addresses`, `get_delivery_slots`
- `prepare_checkout_creation`, `commit_checkout_creation`, `get_checkout`
- `prepare_delivery_selection`, `commit_delivery_selection`
- `prepare_order_submission`, `submit_order`

Las herramientas `prepare_*` no ejecutan la operación. Devuelven un resumen, un `confirmation_id`, una frase exacta y una caducidad de cinco minutos. El `commit_*` correspondiente exige esa frase y consume el identificador una sola vez.

## Comparación realista en Gadis

Cuando se pasa `postal_code`, Gadis consulta su servicio público de cobertura para resolver:

- el surtido que sirve esa zona;
- los gastos de envío;
- el pedido mínimo;
- el importe a partir del cual el envío es gratuito.

Por eso `compare_basket` conserva `total` como subtotal de productos y añade, cuando es verificable:

```json
{
  "subtotal_text": "30.00",
  "delivery": {
    "applied_delivery_fee_text": "4.90",
    "minimum_order_text": "25.00",
    "free_delivery_from_text": "90.00",
    "minimum_order_met": true
  },
  "estimated_checkout_total_text": "34.90"
}
```

`get_delivery_coverage(store="gadis", postal_code="28050")` permite consultar esa política directamente.

## Captura HTTP para convertir Gadis/Froiz a clientes ligeros

El objetivo es terminar con esta arquitectura:

```text
login/anti-bot en Playwright -> cookies/tokens -> cliente HTTP -> carrito y checkout
```

La captura local abre una ventana visible, pero no recibe credenciales como argumentos. Escribe el usuario y contraseña de una cuenta desechable únicamente en la página del supermercado.

Instalación:

```bash
python -m pip install -e ".[dev,browser]"
playwright install chromium
```

Gadis:

```bash
python tools/capture_http_local.py \
  --store gadis \
  --output local-captures/gadis.json
```

Froiz:

```bash
python tools/capture_http_local.py \
  --store froiz \
  --output local-captures/froiz.json
```

El panel negro permite etiquetar estas fases antes de realizarlas manualmente:

1. login;
2. leer cesta;
3. añadir producto;
4. cantidad `1 → 2`;
5. cantidad `2 → 1`;
6. eliminar producto;
7. direcciones;
8. franjas;
9. checkout;
10. entrega;
11. sonda del pedido final.

Durante la última fase todas las escrituras se registran como esquema y se abortan antes de llegar a la tienda. Además, las rutas conocidas de pedido/pago están bloqueadas en cualquier fase.

La captura no escribe un HAR crudo. Conserva métodos, rutas, nombres de headers, códigos HTTP y esquemas, pero elimina en memoria:

- contraseñas y tokens;
- cookies, `Authorization` y CSRF/XSRF;
- emails, teléfonos y direcciones;
- identificadores privados de usuario, carrito, dirección, checkout y pedido;
- valores de query string y datos de pago.

El fichero de sesión se guarda aparte en `~/.open-grocery-mcp/<tienda>/storage_state.json` y nunca entra en el JSON compartible.

Consulta [`docs/http-contract-capture.md`](docs/http-contract-capture.md).

### Resultado de las primeras sondas remotas

- Gadis expone microservicios públicos separados para catálogo, tienda y sesión. La versión `0.4.0` ya reutiliza el servicio de cobertura postal en producción.
- Froiz devuelve `403` a los runners de GitHub en rutas de cesta, por lo que la captura desde la conexión local del propietario es más representativa.
- El flujo remoto publica únicamente manifiestos redactados bajo `diagnostics/http-contracts/` y extrae candidatos de endpoint de los bundles JavaScript sin guardar su código.

## Protecciones de compra

- Las escrituras están deshabilitadas salvo `OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`.
- Cada cambio usa un plan revisado, límite máximo de gasto y comprobación del carrito inmediatamente anterior.
- Tras escribir se vuelve a leer el carrito y se comprueban productos y cantidades.
- Si el total no puede verificarse, se falla de forma cerrada y se intenta restaurar la cesta anterior.
- Las direcciones se devuelven redactadas; tokens, cookies y contraseñas nunca son parámetros MCP.
- Productos de alcohol, tabaco, vapeo o nicotina no se añaden automáticamente.
- Checkout y pedido son acciones separadas.
- Un intento de pedido nunca se reintenta automáticamente si el resultado es ambiguo.
- No se automatizan PSD2, 3-D Secure, SMS, biometría ni confirmaciones bancarias.

El pedido final requiere:

```text
OPEN_GROCERY_ENABLE_RETAILER_WRITES=1
OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1
OPEN_GROCERY_ORDER_APPROVAL_CODE=<secreto local de al menos 6 caracteres>
```

Gadis y Froiz requieren además:

```text
OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1
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

Puede utilizarse Google Chrome instalado:

```text
OPEN_GROCERY_BROWSER_CHANNEL=chrome
OPEN_GROCERY_CAPTURE_BROWSER_CHANNEL=chrome
```

## Sesiones locales

`login_with_browser(store="gadis")`, `login_with_browser(store="froiz")` o `login_with_browser(store="mercadona")` abre un navegador visible. El usuario inicia sesión directamente y completa cualquier verificación.

Las sesiones se guardan en:

```text
~/.open-grocery-mcp/gadis/storage_state.json
~/.open-grocery-mcp/froiz/storage_state.json
~/.open-grocery-mcp/mercadona/storage_state.json
```

Estos archivos equivalen a una sesión iniciada: no deben compartirse ni subirse a Git.

## Ejecución

Solo catálogo y borradores:

```bash
open-grocery-mcp
```

Permitir modificaciones confirmadas:

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

## Flujo recomendado

1. `compare_basket` compara la lista.
2. `prepare_cart` crea un borrador local.
3. `login_with_browser` guarda una sesión.
4. `get_real_cart` devuelve versión y total.
5. `prepare_real_cart_update` produce un plan.
6. `commit_real_cart_update` aplica exactamente ese plan.
7. Checkout y entrega siguen preparar → confirmar → aplicar.
8. El pedido final se prepara y confirma por separado.

## Transporte HTTP

```bash
open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

No expongas esta alpha directamente a Internet. No incorpora identidad multiusuario ni aislamiento de sesiones. Los flujos con navegador están pensados para `stdio` en la máquina del propietario.

## Desarrollo y verificación

```bash
pytest
python -m compileall -q src tests tools
ruff check .
```

Las pruebas automatizadas usan transportes y navegadores simulados. No realizan compras reales.

Consulta [la arquitectura autenticada](docs/authenticated-workflows.md), [los proveedores de navegador](docs/browser-providers.md), [la captura HTTP](docs/http-contract-capture.md), [el contrato de proveedores](docs/provider-contract.md) y [la política de seguridad](SECURITY.md).

## Licencia y procedencia

MIT. Algunas integraciones se apoyan en conocimiento y código de proyectos MIT anteriores; los avisos completos están en [`NOTICE`](NOTICE). Las integraciones no son oficiales y las marcas pertenecen a sus titulares.
