# Open Grocery MCP

Servidor [Model Context Protocol](https://modelcontextprotocol.io/) para buscar productos, comparar una cesta entre supermercados y preparar, con confirmaciones explícitas, cambios en el carrito y el checkout de la cuenta del usuario.

> **Alpha `0.5.0`.** Mercadona usa HTTP para catálogo, carrito, entrega y checkout. Gadis usa HTTP autenticado para sesión, carrito, direcciones, franjas y creación de checkout, con Playwright para login y casos no compatibles. Froiz usa su API Nuxt para carrito, direcciones y calendario; checkout y pedido están deshabilitados porque `orders/create` coloca el pedido real. Eroski combina catálogo público y lectura HTTP con escrituras de carrito verificadas en navegador; su entrega GET-only se limita al contexto ya seleccionado y nunca cambia dirección o tienda de forma implícita.

## Estado actual

| Supermercado | Catálogo | Comparación | Carrito autenticado | Entrega (direcciones/franjas) | Checkout | Pedido final |
|---|---:|---:|---:|---:|---:|---:|
| Gadis | HTTP, por código postal | Sí, con portes y mínimos | HTTP para unidades enteras | HTTP con fallback a navegador | HTTP con confirmación; navegador como fallback | Experimental y apagado |
| Froiz | HTTP autenticado localizado; fallback público no localizado | Sí | Cliente HTTP con relectura, huella y fallback | HTTP (dirección seleccionada + calendario) | No disponible por diseño: no existe frontera separada del pedido | No disponible por diseño |
| Eroski | HTTP/HTML público, no localizado | Sí | Lectura HTTP; escrituras con navegador y doble validación | GET-only para la dirección ya seleccionada | No disponible por diseño: no existe frontera separada del pedido | No disponible por diseño |
| Mercadona | HTTP, por código postal | Sí | HTTP | HTTP | HTTP | Experimental y apagado |

El método para replicar la migración a HTTP con otro supermercado está
documentado en `docs/http-backend-playbook.md`.
La comparación técnica y la evidencia de la auditoría de las cuatro conexiones
están resumidas en `docs/connection-audit.md`.

Ninguna integración se presenta como compra real validada. El endpoint irreversible permanece separado, exige varias autorizaciones locales y no se ejecuta durante pruebas o capturas.

## Qué puede hacer el MCP

Catálogo y comparación:

- `health`, `stores`
- `get_delivery_coverage`
- `search_products`, `search_products_expanded`, `get_product`, `list_categories`
- `explain_product_equivalence`, `explain_product_relationship`, `assess_substitution_candidate`
- `search_offers`, `filter_worthwhile_offers`
- `compare_basket`, `compare_alternatives`, `optimize_basket_combination`
- `catalogue_contracts`, `compare_catalogue_regions`, `semantic_ontology_status`, `audit_semantic_corpus`, `audit_catalogue_quality`
- `prepare_cart`, `get_cart_draft`, `delete_cart_draft`

### Ofertas y alternativas

`search_products_expanded` está pensado para afirmaciones de alta cobertura
como «el más barato» o «todos los productos». Ejecuta una consulta principal y
sus aliases en varios supermercados, une y deduplica los resultados y permite
exigir grupos explícitos de términos. Por ejemplo, para Arzúa-Ulloa se pueden
usar los grupos `[["queso", "queixo"], ["arzua", "ulloa"]]`; así se admite la
variante gallega y se rechaza un vino «Arzuaga». La respuesta incluye consultas
posiblemente saturadas, errores parciales y truncamiento. Un resultado acotado
sin esos indicadores mejora mucho la cobertura, pero no demuestra que el
retailer haya indexado todos sus SKU.

Los contratos, la elección entre búsqueda simple/expandida, las restricciones por
línea y la auditoría reproducible se documentan en
[`docs/semantic-quality.md`](docs/semantic-quality.md).

La expansión usa además perfiles semánticos explicables. Reconoce familias y
facetas relevantes —por ejemplo DOP de queso, corte y formato del jamón,
variedad/preparación del arroz, estilo de yogur, cacao, especie/corte del atún,
preparación del salmón, especie/corte/tratamiento/formato de la carne,
especie/corte/conservación del pescado, especie/formato del marisco,
especie/variedad/estado/formato de frutas y hortalizas, tipo/sabor/gas/azúcar y
formato de bebidas, y tipo, ingrediente, formato, conservación y preparación de
pizzas, croquetas, empanadas, lasañas, ensaladas, sopas, cremas y arroces; además
de harina, edulcorante, sal, salsa, cereales, galletas y especias; uso y forma de
detergentes y limpieza; subtipos de higiene, alimentación infantil y mascotas;
y formas ampliadas de pasta, pan, tofu, huevo, yogur, café, jamón y salmón—. Las variantes
compatibles amplían la búsqueda y los conflictos explícitos se excluyen aunque
compartan palabras. `explain_product_equivalence` devuelve los perfiles,
conflictos y aspectos todavía inciertos de dos descripciones sin consultar ni
modificar ningún carrito.

`search_offers` devuelve únicamente promociones observadas en el catálogo del
supermercado. Calcula el coste efectivo para la cantidad solicitada en reglas
explícitas como descuento directo, lote, 2x1 o precio por volumen. Una etiqueta
sin cifras suficientes se muestra como descriptiva y nunca abarata el cálculo.
Los precios de fidelización solo se aplican con `include_loyalty=true`; los
cupones personales y las promociones no observables hasta checkout se excluyen.

`filter_worthwhile_offers` contrasta cada producto promocionado con el artículo
actual más barato que supere un umbral conservador de similitud. Normaliza el
precio a €/kg, €/litro o €/unidad física, contempla otras marcas y reconoce las
principales marcas blancas de cada cadena. Las promociones por cantidad se
evalúan por defecto en el mínimo necesario para activarlas. El resultado separa
ofertas que compensan, ofertas superadas por una alternativa y casos que no se
pueden verificar con una unidad comparable.

`compare_basket` usa el mismo cálculo por cantidad. `compare_alternatives`
permite contrastar alimentos sustituibles y devuelve por separado el coste del
envase, el precio comparable por kg/litro cuando puede demostrarse, y el coste
por una cantidad objetivo de nutriente. Esta última clasificación solo se crea
si el supermercado declara ese nutriente por 100 g/ml; no sustituye consejo
dietético o médico.

La búsqueda de ofertas no inicia Playwright. Mercadona consulta su índice HTTP
por almacén; Gadis usa el catálogo JSON de la tienda que sirve el código postal;
Eroski descarga una única página pública de resultados y la procesa localmente;
Froiz usa su API autenticada solo con el token ya disponible y cae al índice
público sin intentar abrir Chromium. En Froiz, la tienda resuelta se conserva
15 minutos y un fallo de autenticación no vuelve a probarse durante 60 segundos.
El navegador queda reservado para un login solicitado expresamente y para los
flujos autenticados que no dispongan de una frontera HTTP segura.

Cuenta y compra:

- `account_status`, `login_with_browser`, `import_browser_session`
- `get_real_cart`
- `prepare_real_cart_update`, `prepare_clear_real_cart`, `commit_real_cart_update`
- `list_delivery_addresses`, `get_delivery_slots`
- `prepare_checkout_creation`, `commit_checkout_creation`, `get_checkout`
- `prepare_delivery_selection`, `commit_delivery_selection`
- `prepare_human_handoff`, `open_human_review`
- `prepare_order_submission`, `submit_order`

Las herramientas `prepare_*` no modifican el supermercado. Devuelven un resumen, un `confirmation_id`, una frase exacta y una caducidad de cinco minutos. El `commit_*` correspondiente exige esa frase y consume el identificador una sola vez. Las herramientas de entrega y checkout solo están disponibles cuando `stores()` anuncia la capacidad correspondiente.

`prepare_human_handoff` es la frontera operativa recomendada. En Mercadona y
Gadis relee un checkout con total, dirección y franja todavía válidos. En Froiz
y Eroski relee la cesta y, opcionalmente, la franja, pero se detiene antes del
primer write de checkout porque ese límite no está separado de crear el pedido.
`open_human_review` repite esa validación y abre una ventana autenticada en la
pantalla más avanzada segura. El MCP solo navega mediante GET y no pulsa ningún
control; la acción final pertenece siempre a la persona.

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
- cantidades o respuestas que el contrato HTTP no pueda verificar con seguridad;
- el pedido final, deliberadamente fuera de las pruebas reversibles.

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

Los proveedores que anuncian envío final mediante navegador —actualmente
Gadis— requieren además:

```text
OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1
```

Estas opciones no deben activarse durante desarrollo o captura.
No habilitan checkout ni pedido en Froiz o Eroski, donde esas operaciones
están bloqueadas por diseño.

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

`login_with_browser(store="gadis")`, `login_with_browser(store="froiz")`, `login_with_browser(store="eroski")` o `login_with_browser(store="mercadona")` abre un navegador visible. El usuario inicia sesión directamente y completa cualquier verificación. Froiz y Eroski guardan la sesión automáticamente solo cuando aparece un control de cuenta exclusivo de una sesión autenticada.

Las sesiones se guardan en:

```text
~/.open-grocery-mcp/gadis/storage_state.json
~/.open-grocery-mcp/froiz/storage_state.json
~/.open-grocery-mcp/eroski/storage_state.json
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

## Despliegue remoto

La instancia oficial de solo lectura se publica en Vercel y es de **acceso público**:

```text
MCP:    https://open-grocery-mcp.vercel.app/mcp
Salud:  https://open-grocery-mcp.vercel.app/health
```

El endpoint `/mcp` es accesible sin autenticación. El adaptador ASGI es stateless
y proporciona acceso público a catálogo, comparación de cestas, cobertura de entrega
y ofertas. En este entorno permanecen apagadas `OPEN_GROCERY_ENABLE_RETAILER_WRITES`,
`OPEN_GROCERY_ENABLE_ORDER_SUBMISSION` y `OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION`.

El proyecto de Vercel está conectado a este repositorio. Cada push a `main`
construye producción y actualiza el dominio estable; otras ramas crean previews.
La instancia desplegada no depende de una copia local ni de que el ordenador del
propietario permanezca encendido.

Las sesiones de supermercados, Playwright y los flujos autenticados siguen siendo
exclusivamente locales. No subas `storage_state.json`, `.env`, capturas, HAR ni
perfiles de navegador. Consulta [`docs/vercel-deployment.md`](docs/vercel-deployment.md)
para despliegue y verificación.

## Flujo recomendado

1. `compare_basket` compara la lista.
2. `prepare_cart` crea un borrador local.
3. `login_with_browser` guarda una sesión si todavía no existe.
4. `get_real_cart` devuelve versión, líneas y total.
5. `prepare_real_cart_update` produce un plan sin escribir.
6. El usuario revisa la frase exacta.
7. `commit_real_cart_update` aplica únicamente ese plan y vuelve a verificarlo.
8. Checkout y entrega siguen preparar → confirmar → aplicar.
9. `prepare_human_handoff` revalida la última frontera segura y
   `open_human_review` abre la ventana sin hacer clic.
10. La persona realiza cualquier acción final; pedido y pago quedan fuera de la
    automatización de cierre.

## Captura HTTP y trabajo con agentes locales

Las capturas autenticadas deben ejecutarse en la máquina del propietario, pero un agente local con terminal y navegador puede encargarse de ellas sin convertir el proceso en una lista manual.

Lee:

- [`AGENTS.md`](AGENTS.md)
- [`docs/local-agent-handoff.md`](docs/local-agent-handoff.md)
- [`docs/http-contract-capture.md`](docs/http-contract-capture.md)
- [`docs/automation-completion.md`](docs/automation-completion.md)

El agente debe validar siempre el JSON con:

```powershell
python .\tools\validate_capture.py `
  .\local-captures\gadis-authenticated.json `
  --minimum-events 10 `
  --require-response `
  --require-cart-write `
  --require-restored-cart `
  --fail-on-reported-errors
```

Una captura con `events: 0`, errores de acción o sin un write de carrito
observable es un fallo que debe depurarse; nunca se acepta como validación de
una mutación autenticada.

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

Consulta también [el despliegue en Vercel](docs/vercel-deployment.md), [la arquitectura autenticada](docs/authenticated-workflows.md), [los proveedores de navegador](docs/browser-providers.md), [el contrato de proveedores](docs/provider-contract.md) y [la política de seguridad](SECURITY.md).

## Licencia y procedencia

MIT. Algunas integraciones se apoyan en conocimiento y código de proyectos MIT anteriores; los avisos completos están en [`NOTICE`](NOTICE). Las integraciones no son oficiales y las marcas pertenecen a sus titulares.
