# Open Grocery MCP

Servidor [Model Context Protocol](https://modelcontextprotocol.io/) para buscar productos, comparar una cesta entre supermercados y, cuando existe una integración autenticada verificada, preparar el carrito, la entrega y el checkout con límites de gasto y confirmaciones explícitas.

> **Alpha `0.2.0`.** Gadis y Froiz ofrecen catálogo/comparación. Mercadona añade sesión local, carrito real, direcciones, franjas y checkout. El envío definitivo del pedido está implementado como función experimental, apagada por defecto, y todavía no se ha validado realizando una compra real desde este repositorio.

## Estado actual

| Supermercado | Catálogo | Comparar | Carrito real | Entrega/checkout | Pedido final |
|---|---:|---:|---:|---:|---:|
| Gadis | Sí | Sí | Pendiente | Pendiente | No |
| Froiz | Sí | Sí | Pendiente | Pendiente | No |
| Mercadona | Sí | Sí | Sí | Sí | Experimental |

No se anuncian capacidades autenticadas de Gadis o Froiz hasta observar y probar sus peticiones con una sesión legítima del usuario. No basta con adivinar rutas a partir de la interfaz.

## Herramientas

Catálogo y comparación:

- `health`, `stores`
- `search_products`, `get_product`, `list_categories`
- `compare_basket`
- `prepare_cart`, `get_cart_draft`, `delete_cart_draft`

Mercadona autenticado:

- `account_status`, `login_with_browser`, `import_browser_session`
- `get_real_cart`
- `prepare_real_cart_update`, `prepare_clear_real_cart`, `commit_real_cart_update`
- `list_delivery_addresses`, `get_delivery_slots`
- `prepare_checkout_creation`, `commit_checkout_creation`, `get_checkout`
- `prepare_delivery_selection`, `commit_delivery_selection`
- `prepare_order_submission`, `submit_order`

Las herramientas `prepare_*` no escriben en la tienda. Devuelven un resumen, un `confirmation_id`, una frase exacta y una caducidad de cinco minutos. El `commit_*` correspondiente exige esa frase, consume el identificador una sola vez y vuelve a verificar el estado remoto.

## Protecciones de compra

- Los cambios autenticados están deshabilitados salvo que se active `OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`.
- El carrito usa la versión revisada para detectar cambios concurrentes.
- Tras escribir, se espera hasta que las líneas remotas coincidan con el plan aprobado.
- El total se vuelve a leer. Si supera el máximo, se intenta restaurar el carrito anterior.
- Direcciones, franjas y total se revisan de nuevo antes del pedido.
- El envío final requiere además `OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1` y un código local independiente de al menos seis caracteres.
- No se aceptan contraseñas, cookies o tokens como parámetros MCP.
- No se automatiza una autenticación bancaria o desafío de la tienda.

Ejemplo de frase para cambiar el carrito:

```text
CONFIRMAR CARRITO 52.38 EUR
```

Ejemplo de frase irreversible:

```text
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
pytest
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
pytest
```

El extra `browser` instala Playwright para abrir una ventana visible de Chrome. El usuario escribe sus credenciales directamente en Mercadona; el MCP solo conserva el `storage_state.json` resultante en:

```text
~/.open-grocery-mcp/mercadona/storage_state.json
```

Ese archivo contiene una sesión sensible. No debe subirse a Git ni compartirse.

## Ejecución

Solo catálogo y borradores locales:

```bash
open-grocery-mcp
```

Permitir cambios confirmados de carrito/checkout:

```bash
open-grocery-mcp --allow-retailer-writes
```

Permitir también el endpoint final, únicamente en la máquina del propietario:

```bash
export OPEN_GROCERY_ORDER_APPROVAL_CODE='un-codigo-local-largo'
open-grocery-mcp --allow-retailer-writes --allow-order-submission
```

Configuración genérica de cliente MCP:

```json
{
  "mcpServers": {
    "open-grocery": {
      "command": "/ruta/al/entorno/bin/open-grocery-mcp",
      "env": {
        "OPEN_GROCERY_ENABLE_RETAILER_WRITES": "1"
      }
    }
  }
}
```

## Flujo recomendado

1. `compare_basket` compara la lista entre las tiendas disponibles.
2. `prepare_cart` guarda un borrador local del supermercado elegido.
3. `login_with_browser` abre Chrome para iniciar sesión.
4. `get_real_cart` obtiene la versión y el total actuales.
5. `prepare_real_cart_update` genera el plan y la frase; el usuario lo revisa.
6. `commit_real_cart_update` aplica exactamente ese plan.
7. Se consultan direcciones y franjas.
8. Checkout y entrega siguen el mismo patrón preparar → confirmar → aplicar.
9. El pedido final se prepara por separado y permanece desactivado salvo triple aprobación.

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

La comparación normaliza precio por kg, litro o unidad cuando la tienda lo facilita. Los resultados pueden no ser SKU idénticos y deben revisarse. Gastos de envío, pedido mínimo, cupones personales y sustituciones solo se incluyen cuando el proveedor los expone de forma verificable.

## Transporte HTTP

```bash
open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

El endpoint es `/mcp`. No lo expongas directamente a Internet: esta alpha no incorpora identidad multiusuario ni aislamiento de sesiones. Usa un proceso por usuario, una red privada o un proxy autenticado con TLS y límites de uso.

## Desarrollo y pruebas

```bash
pytest
python -m compileall -q src tests
ruff check .
```

Las pruebas usan respuestas HTTP simuladas y cubren sesión, refresh, carrito, límite de gasto, rollback, control de versión, confirmaciones de un uso, checkout y bloqueo del pedido. No realizan compras reales.

La única forma de afirmar que el último POST funciona de extremo a extremo es ejecutar deliberadamente un pedido real de bajo importe que el propietario quiera comprar. Hasta entonces, el endpoint se etiqueta como experimental aunque el flujo y sus contratos estén implementados.

Consulta [la arquitectura autenticada](docs/authenticated-workflows.md), [el contrato de proveedores](docs/provider-contract.md) y [la política de seguridad](SECURITY.md).

## Licencia y procedencia

MIT. Algunas integraciones se apoyan en conocimiento y código de proyectos MIT anteriores; los avisos completos están en [`NOTICE`](NOTICE). Las integraciones no son oficiales y las marcas pertenecen a sus titulares.
