# Playbook: migrar un supermercado de navegador a HTTP autenticado

Este documento resume, paso a paso, el método que se siguió para llevar a
Gadis de un backend 100 % Playwright al actual híbrido (HTTP para sesión,
carrito, mutaciones, franjas y creación de checkout; navegador solo para
login, cantidades fraccionarias y casos raros). Sirve como receta replicable
para cualquier otro supermercado. Froiz ya dispone de cliente HTTP para
carrito y entrega; Eroski conserva navegador donde su contrato Tapestry no
ofrece una transición HTTP segura y reproducible.

La regla de oro: **derivar el contrato desde evidencia real y sanitizada,
implementar fail-closed, probar con mocks y verificar en vivo solo lo
reversible**. Nunca se automatiza el pedido final ni el pago.

---

## Fase 0 · Inventario de evidencia local

Antes de abrir un navegador, revisa lo que ya existe en la máquina:

```text
~/.open-grocery-mcp/<store>/storage_state.json   # sesión guardada (nunca imprimir)
local-captures/*.json                            # capturas previas sanitizadas
diagnostics/http-contracts/                      # contratos publicados
diagnostics/apk/*.json                           # análisis de APK si existe
```

Comprueba la salud de la sesión sin exponer valores:

```powershell
python .\tools\verify_gadis_http_local.py
```

`ok=true` con `cart_backend="gadis_http"` significa que las cookies NextAuth
sirven para lecturas y mutaciones. Si falla, pide al propietario un
`login_with_browser` visible: es el único paso humano permitido.

## Fase 1 · Captura autenticada sanitizada

```powershell
$env:OPEN_GROCERY_CAPTURE_HEADLESS = "1"
python .\tools\capture_http_contract.py `
  --store gadis `
  --mode authenticated `
  --output .\local-captures\<store>-auth.json

python .\tools\validate_capture.py `
  .\local-captures\<store>-auth.json `
  --minimum-events 10 `
  --require-response `
  --require-cart-write `
  --require-restored-cart `
  --fail-on-reported-errors
```

El probe registra peticiones/respuestas con `shape()`: claves JSON y tipos,
valores privados sustituidos por `<redacted>`/`<id>`. Las rutas de pedido/pago
que encajan con `DANGEROUS` se abortan antes de salir del navegador.
Los tres últimos requisitos son obligatorios para afirmar que una mutación
reversible quedó validada; una secuencia visual verde sin un write HTTP
observable se considera una captura fallida.

Para flujos profundos (checkout) existe una variante extensible:
`tools/capture_gadis_delivery_contract.py`, que añade fases
(`addresses_page`, `checkout_open`, `schedule_select`, `checkout_create`)
con bloqueo reforzado (`ORDER_BLOCK`) y prohibición de pulsar controles de
pago. Copia ese patrón para otra tienda.

## Fase 2 · Análisis estático de los bundles JS (la técnica más rentable)

Los bundles de la web son públicos. Con los `script_sources` y
`bundle_candidates` de cualquier captura (o descargando los chunks del build):

1. Descarga los chunks referenciados (GET públicos, sin sesión).
2. Extrae literales tipo `"api/config/..."`, `"/carts/"`, `"/orders"` etc.
3. Busca el objeto mapa de rutas (en Gadis, chunk `2709-*.js`, variable
   agrupada con bases `Vo/VI/Gs`): define TODOS los endpoints y sus envoltorios.
4. Localiza los puntos de llamada (`postCheckout`, `putSchedule`,
   `deleteScheduleSession`, `postCheckoutAndUpdateSession`…) para deducir
   método, cuerpo exacto y cabeceras.

Resultado típico (Gadis): mapa completo de `/carts/{id}`,
`/carts/{id}/addresses`, `/carts/{id}/schedule`,
`/carts/{id}/checkout`, `stores/{id}/calendar`,
`clients/{id}/addresses`, además de los envoltorios www
`/api/config/{updateProduct,updateCart,updateSchedule,deleteSchedule,checkout}`.

Un análisis de APK (`tools/analyze_apk.py` → `diagnostics/apk/`) complementa
esto con endpoints de apps móviles, pero suele aportar menos que el bundle.

## Fase 3 · Derivar el modelo de autenticación

Distingue siempre dos planos (Gadis los separa así):

| Plano | Host | Autenticación | Ejemplos |
|---|---|---|---|
| Tienda Next.js | `www.<retailer>.com/api/config/*` | Cookies de sesión | updateProduct, updateCart, deleteSchedule, checkout |
| Microservicios | `<svc>.gadisline.com/api/v3` | `Authorization: Bearer <JWT>` + `site-id`/`store-id`/`accept-language` | calendar, carts, clients |

Lecciones medidas durante la integración de Gadis:

- El bearer de `/api/auth/session` puede estar **caducado** aunque la sesión
  cookie siga sirviendo carrito. La ruta `/api/config/getToken` devuelve
  `{}` cuando el refresco server-side falla (y su POST llega a dar 504).
- Los microservicios Tomcat rechazan con **400** cuando la cabecera Cookie
  total + Bearer supera ~8 KB: el navegador nunca manda cookies a esos hosts;
  el cliente HTTP tampoco debe hacerlo.
- Rutas muertas: literales del bundle pueden no existir server-side
  (`/api/config/updateSchedule` responde 404). Verifica cada ruta nueva con
  una prueba inocua (cuerpo vacío ⇒ debe responder 400/500 de validación,
  nunca 401/404 de auth/routing) antes de implementarla.
- El campo `last_modified_date` del carrito avanzaba con CADA lectura: no
  sirve como versión optimista. Se sustituyó por una huella determinista del
  contenido (ver `GadisHTTPClient._stable_version`).

## Fase 4 · Implementar el adaptador

Capas, en este orden:

1. **Cliente HTTP** (`providers/<store>_http.py`): métodos de bajo nivel,
   normalización value-free y *fallback* www→microservicio cuando aplique.
2. **Mezcla de cuenta** (`providers/<store>_account.py`): expone
   `real_cart/preview_cart_update/commit_cart_update/addresses/slots/
   preview_checkout/create_checkout`; intenta HTTP primero y cae a navegador
   solo con `(AuthenticationRequired, ProviderError)` explícitos.
3. **Workflow** (`workflow_checkout.py`): `prepare_checkout_creation`
   acepta el trío de entrega opcional (`shipping_address_id`,
   `delivery_date`, `schedule_range_id`); parcial ⇒ `InvalidRequest`.
4. **Herramientas MCP** (`authenticated_tools.py`): documenta los parámetros.

Reglas fail-closed innegociables (ver `docs/provider-contract.md`):

- relectura del estado tras cada escritura;
- versión/huella esperada en cada mutación (`ConcurrentCartChange`);
- tope de gasto verificado tras escribir;
- rollback reversible si la fase posterior falla
  (`_create_http_checkout`: schedule→create→`delete_schedule` en except);
- creación de checkout y envío de pedido permanecen separados; el adaptador
  de checkout seguro no llama a `/orders`. Los métodos de pedido final son
  independientes, experimentales y quedan apagados durante pruebas y capturas.

## Fase 5 · Tests con mocks

Patrones ya usados en `tests/test_gadis_http.py` y `tests/test_gadis_account.py`:

- `httpx.MockTransport` para rutas por host (session/site/calendar/carts),
  afirmando path, cabecera Authorization y cuerpo exacto.
- Fakes a nivel mixin para simular timestamp volátil, cambio concurrente,
  fallo de servidor y rollback.
- Un test que afirma que el flujo de carrito/checkout seguro **nunca** toca
  `/orders`.
- Tests de workflow: el trío de entrega queda embebido en la confirmación
  de una sola frase; el triple incompleto se rechaza.

```powershell
python -m pytest -q
python -m compileall -q src tests tools
ruff check .
git diff --check
```

## Fase 6 · Verificación en vivo reversible

Solo en la máquina con sesión válida, con doble autorización:

```powershell
$env:OPEN_GROCERY_ENABLE_RETAILER_WRITES = "1"
python .\tools\verify_gadis_delivery_local.py `
  --allow-reversible-schedule-write
# variante completa (prepara/restaura el resumen; jamás llama a pago/pedido):
python .\tools\verify_gadis_delivery_local.py `
  --allow-reversible-schedule-write `
  --allow-checkout-summary
```

Criterios de aceptación: `calendar_read`, `addresses_read`,
`schedule_applied`, `checkout_summary_prepared`, `schedule_removed`, `state_restored=true`,
`order_or_payment_attempted=false`. Una restauración fallida detiene todo.

## Diagnóstico rápido de fallos comunes

| Síntoma | Causa probable | Acción |
|---|---|---|
| `AuthenticationRequired` en microservicios | bearer caducado; getToken `{}` o 504 | login visible del propietario |
| 400 HTML de Tomcat | jarra de cookies entera + bearer (>8 KB) | no enviar cookies a microservicios |
| 400 con URL válida | id de store del bootstrap no válido para esa API | usar el `store_id` del carrito real |
| 404 en ruta `/api/config/*` | ruta muerta del bundle | buscar el llamador real en el JS |
| `ConcurrentCartChange` sistemático | versión derivada de campo volátil | huella de contenido estable |
| captura con `events: 0` | listeners tardíos o anti-bot | validar y depurar según AGENTS.md |

### Lecciones de Froiz (Nuxt SPA con API propia)

- **Rotación de token por arranque**: la SPA rota su OAuth access token en
  cada carga y lo guarda en memoria/sessionStorage; la copia en cookie del
  `storage_state` queda invalidada. Patrón que funciona: un *bootstrap*
  headless abre la sesión guardada, intercepta la primera llamada a la API y
  captura el header `Authorization` fresco para el cliente HTTP puro.
- **Cookies URL-codificadas**: el valor de `auth._token.*` llega
  percent-encoded; aplicar `urllib.parse.unquote` antes de usarlo.
- **Caché local ligada a la sesión**: el bearer cacheado lleva una huella del
  `storage_state`, caduca y se guarda con permisos del propietario. Un cambio
  de sesión invalida la caché. Los `401` solo permiten refresh/retry en lecturas;
  una escritura nunca se repite automáticamente.
- **Descubrir el host real**: el axios del SPA define `browserBaseURL`
  (aquí `https://servicios.froiz.com`, distinto del host web). Búscalo en el
  bundle antes de probar rutas contra el dominio equivocado (respondería el
  fallback SSR con HTML).
- **Carrito desechable**: si la API permite crear y borrar carritos enteros
  (`POST /api/cart` y `DELETE /api/cart/{id}`), verifica las mutaciones sobre
  un carrito desechable propio: cero contacto con el carrito real del usuario.
  La limpieza solo se intenta tras relecturas estables que demuestren identidad
  y estado exactos; si no, se detiene para revisión manual. Usa
  `channel_cart_untouched=true` como criterio.
- **Sin contador de versiones**: usa huella determinista del contenido desde
  el primer día y trata "sin carrito ligado" (`cartId: null`) como carrito
  vacío creable con POST, igual que hace la SPA.
- **Frontera de checkout**: `orders/create` coloca el pedido real. Por tanto,
  Froiz anuncia carrito y entrega, pero no checkout ni pedido; los métodos
  internos fallan explícitamente sin abrir el navegador.

### Lecciones de Eroski (Tapestry 5 server-rendered)

- **Plataforma**: Apache Tapestry 5 con rutas componente`:acción`
  (`/es/<page>.<layout>.<component>:<event>`), CSRF firmado `t:formdata`
  por página y sesión `JSESSIONID`. No hay API JSON: todo son submits de
  formulario URL-encoded.
- **Sesión**: la cesta exige login (la anónima redirige a identificación).
  Login con 2FA por SMS en `areacliente.eroski.es`; SSO posterior hacia
  `areaprivada.eroski.es/es/mis-datos` (direcciones).
- **Añadir**: `POST /es/search/results.productlist.productlistitem_N.
  productlistadditem:addtocart?q=<búsqueda>` con `q` + `t:formdata`; el
  disparador real es un enlace `a.update.toAddProduct` dentro del tile
  (el `<button>` del form es decorativo). Respuesta 301 + zonas
  `:notify`/:refreshevent`.
- **Quitar/cantidad**: `POST /es/mycart.basket.productlist.basketproduct.
  basketadditemcomponent:addtocart` con `product=<id>` y zonas; se dispara
  con el enlace «Eliminar item» (`a.remove-item-shopping-btn-cart`).
- **Entrega**: el flujo autenticado de recogida quedó capturado y validado
  hasta seleccionar tienda y franja. La tienda usa los eventos Tapestry
  `addresslistselector:update_map` y `addresslistselector:address_select`;
  la franja se confirma con `POST ...pickupaddressselector.slotform` y los
  campos firmados `selectedAddressRef`, `selectedSlotRef_0`,
  `selectedSlotTime_0` y `t:formdata`. Los identificadores concretos no se
  conservan fuera de la captura local sanitizada.
- **Frontera de seguridad**: no existe paso de checkout separado;
  `orders/create` coloca el pedido real ⇒ checkout automatizado prohibido.
- **Capabilities honestas**: Eroski anuncia un `delivery` GET-only que puede
  describir direcciones y franjas únicamente para el contexto ya seleccionado.
  Pedir slots de otra dirección falla cerrado porque seleccionarla exige POST
  Tapestry con estado firmado; no anuncia `checkout`.
- **Condición previa para escrituras headless**: el servidor exige que la sesión tenga
  un contexto de entrega (tienda + modo + dirección + franja) establecido
  ANTES de aceptar cualquier `addtocart`. Sin él, el POST devuelve 200 con
  `{"_tapestry": {"redirectURL": ".../es/login/delivery/"}}` y el carrito
  permanece vacío. Este contexto se establece mediante el flujo
  `bookingdelivery` multi-paso, que por contrato requiere navegación real. La
  captura autenticada de recogida validó 329 eventos (236 peticiones y 93
  respuestas). Con ese contexto, el alta HTTP directa quedó verificada; la
  retirada HTTP no, porque el HTML vivo del carrito no expone el config de
  componente que necesita `set_item_quantity`. El flujo público del MCP se
  verificó de extremo a extremo con fallback de navegador: preparar → añadir
  → releer por HTTP → retirar → comprobar restauración. La navegación para
  establecer entrega y la retirada por navegador son decisiones de backend
  explícitas, no tareas HTTP abiertas; el envío de pedido permanece bloqueado.

## Checklist para replicar en otro supermercado

1. Salud de sesión y captura base validadas (Fase 0–1).
2. Mapa de rutas extraído de bundles con llamadores (Fase 2).
3. Modelo de auth documentado (cookies vs bearer; límites de cabeceras).
4. Adaptador por capas con fallback y reglas fail-closed (Fase 4).
5. Suite de mocks verde incluyendo "nunca /orders" (Fase 5).
6. Verificación en vivo reversible con informe value-free (Fase 6).
7. README, `provider-contract.md` y PR actualizados con el estado real.
