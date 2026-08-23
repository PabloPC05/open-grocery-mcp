# Open Grocery MCP

Servidor [Model Context Protocol](https://modelcontextprotocol.io/) para buscar productos, comparar una cesta entre supermercados y preparar, con confirmaciones explícitas, cambios en el carrito y el checkout de la cuenta del usuario.

> **Alpha `0.5.0`.** Mercadona utiliza HTTP para su flujo autenticado. Gadis ya utiliza HTTP para verificar la sesión, leer el carrito, aplicar mutaciones reversibles de cantidades enteras, consultar direcciones y franjas y crear el checkout; conserva Playwright para login, cantidades fraccionarias y casos en que el bearer de microservicios está caducado. Froiz lee su carrito, aplica mutaciones reversibles sobre un carrito desechable y consulta direcciones y franjas por HTTP (contrato Nuxt verificado en vivo); conserva Playwright para login y checkout, que sigue bloqueado por diseño porque su API salta directamente a crear el pedido real. Eroski lee su carrito por HTTP; las escrituras requieren navegador porque el servidor exige contexto de entrega por sesión antes de aceptar mutaciones (documentado en `docs/http-backend-playbook.md`).

## Estado actual

| Supermercado | Catálogo | Comparación | Carrito autenticado | Entrega (direcciones/franjas) | Checkout | Pedido final |
|---|---:|---:|---:|---:|---:|---:|
| Gadis | HTTP, por código postal | Sí, con portes y mínimos | HTTP para unidades enteras | HTTP con fallback a navegador | HTTP con confirmación; navegador como fallback | Experimental y apagado |
| Froiz | Sí | Sí | HTTP sobre carrito desechable; navegador como fallback | HTTP (direcciones + calendario) con fallback a navegador | Navegador; bloqueado por diseño (su API crea pedidos reales) | Experimental y apagado |
| Eroski | Sí (Empathy.co) | Sí | Lectura HTTP; escrituras requieren navegador (contexto de entrega por sesión) | Navegador | Navegador; bloqueado por diseño (su API crea pedidos reales) | No implementado |
| Mercadona | HTTP, por código postal | Sí | HTTP | HTTP | HTTP | Experimental y apagado |

El método para replicar la migración a HTTP con otro supermercado está
documentado en `docs/http-backend-playbook.md`.

Ninguna integración se presenta como compra real validada. El endpoint irreversible permanece separado, exige varias autorizaciones locales y no se ejecuta durante pruebas o capturas.

## Qué puede hacer el MCP

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

Las herramientas `prepare_*` no modifican el supermercado. Devuelven un resumen, un `confirmation_id`, una frase exacta y una caducidad de cinco minutos. El `commit_*` correspondiente exige esa frase y consume el identificador una sola vez.

## Arquitectura autenticada de Gadis

La captura local confirmó este flujo:

```text
login en Playwright
        ↓
storage_state local
        ↓
NextAuth /api/auth/session
        ↓
token Keycloak mantenido solo en memoria
        ↓
servicios HTTP de Gadis
```

La versión `0.5.0` usa ese contrato para:

- comprobar si la sesión sigue autenticada;
- leer el carrito sin abrir Chromium;
- añadir, cambiar o retirar productos de cantidad entera;
- verificar producto, cantidad y total después de cada escritura;
- detectar cambios concurrentes mediante la versión del carrito;
- restaurar la cesta anterior cuando una escritura o el total no se pueden verificar;
- aceptar una respuesta de escritura perdida únicamente cuando una lectura segura demuestra que el estado deseado sí quedó aplicado.

Se mantiene el navegador para:

- iniciar sesión, CAPTCHA o 2FA;
- productos vendidos en cantidades fraccionarias;
- selección de direcciones y franjas mientras no exista un identificador HTTP utilizable sin exponer datos privados;
- creación y manejo del checkout;
- cualquier parte todavía no validada del pedido final.

Antes de pasar del carrito HTTP al checkout del navegador, el proveedor compara:

1. los productos;
2. las cantidades;
3. el total;
4. la versión HTTP revisada.

Solo después traduce esa revisión a la versión interna de la página. Así se evita abrir un checkout con una cesta distinta o rechazar falsamente una cesta correcta porque HTTP y navegador calculan versiones de forma diferente.

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

También puede consultarse directamente:

```text
get_delivery_coverage(store="gadis", postal_code="28050")
```

## Protecciones de compra

- Las escrituras están deshabilitadas salvo `OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`.
- Cada cambio usa un plan revisado, límite máximo de gasto y comprobación de versión inmediatamente anterior.
- Tras escribir se vuelve a leer el carrito y se comprueban productos, cantidades y total.
- Si el total excede el límite o el estado no coincide, se falla de forma cerrada y se intenta restaurar la cesta anterior.
- Una respuesta ambigua no se reintenta automáticamente.
- Las direcciones se devuelven redactadas; tokens, cookies y contraseñas nunca son parámetros MCP.
- Alcohol, tabaco, vapeo y nicotina no se añaden automáticamente.
- Checkout y pedido son acciones separadas.
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

Estas opciones no deben activarse durante desarrollo o captura.

## Instalación

Requiere Python 3.11 o posterior. El desarrollo actual está en `feat/initial-mcp`.

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
.\.venv\Scripts\Activate.ps1
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

En Windows pueden almacenarse bajo `%LOCALAPPDATA%\open-grocery-mcp`. Estos archivos equivalen a una sesión iniciada: no deben compartirse, abrirse en un chat ni subirse a Git.

## Ejecución

Solo catálogo, comparación y borradores:

```bash
open-grocery-mcp
```

Permitir modificaciones confirmadas del carrito:

```bash
open-grocery-mcp --allow-retailer-writes
```

Transporte HTTP local:

```bash
open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

No expongas esta alpha directamente a Internet. No incorpora identidad multiusuario ni aislamiento de sesiones. Los flujos con navegador están pensados para `stdio` en la máquina del propietario.

## Flujo recomendado

1. `compare_basket` compara la lista.
2. `prepare_cart` crea un borrador local.
3. `login_with_browser` guarda una sesión si todavía no existe.
4. `get_real_cart` devuelve versión, líneas y total.
5. `prepare_real_cart_update` produce un plan sin escribir.
6. El usuario revisa la frase exacta.
7. `commit_real_cart_update` aplica únicamente ese plan y vuelve a verificarlo.
8. Checkout y entrega siguen preparar → confirmar → aplicar.
9. El pedido final se mantiene separado y deshabilitado salvo autorización extraordinaria.

## Captura HTTP y trabajo con agentes locales

Las capturas autenticadas deben ejecutarse en la máquina del propietario, pero un agente local con terminal y navegador puede encargarse de ellas sin convertir el proceso en una lista manual.

Lee:

- [`AGENTS.md`](AGENTS.md)
- [`docs/local-agent-handoff.md`](docs/local-agent-handoff.md)
- [`docs/http-contract-capture.md`](docs/http-contract-capture.md)

El agente debe validar siempre el JSON con:

```powershell
python .\tools\validate_capture.py `
  .\local-captures\gadis-authenticated.json `
  --minimum-events 5 `
  --require-response
```

Una captura con `events: 0` es un fallo que debe depurarse; nunca se acepta como resultado parcial.

La captura no genera un HAR crudo. Conserva métodos, rutas, nombres de cabeceras, códigos HTTP y esquemas, pero elimina en memoria:

- contraseñas y tokens;
- cookies, `Authorization` y CSRF/XSRF;
- emails, teléfonos y direcciones;
- identificadores privados de usuario, carrito, dirección, checkout y pedido;
- valores de query string y datos de pago.

## Desarrollo y verificación

```bash
pytest
python -m compileall -q src tests tools
ruff check .
```

Las pruebas automatizadas usan transportes y navegadores simulados. No realizan compras reales.

Consulta también [la arquitectura autenticada](docs/authenticated-workflows.md), [los proveedores de navegador](docs/browser-providers.md), [el contrato de proveedores](docs/provider-contract.md) y [la política de seguridad](SECURITY.md).

## Licencia y procedencia

MIT. Algunas integraciones se apoyan en conocimiento y código de proyectos MIT anteriores; los avisos completos están en [`NOTICE`](NOTICE). Las integraciones no son oficiales y las marcas pertenecen a sus titulares.
