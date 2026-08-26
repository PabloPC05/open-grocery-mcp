# Auditoría de conexiones de supermercado

Fecha: 2026-08-24. La revisión separa capacidad implementada, evidencia pública
viva, evidencia autenticada local y límites deliberados de seguridad. No se
equiparan mocks o tráfico guest con una sesión autenticada.

## Resultado por conexión

| Tienda | Catálogo | Carrito autenticado | Entrega | Checkout/pedido |
|---|---|---|---|---|
| Mercadona | HTTP localizado por CP; search/product/categorías | HTTP con versión, `sources`, relectura, tope y rollback | HTTP | Checkout HTTP; pedido experimental con opt-ins y marcador persistente |
| Gadis | HTTP localizado, categorías y cobertura | HTTP para unidades enteras; fallback seguro para fraccionarias | HTTP con fallback y contexto de dirección | Resumen reversible hasta tarjeta; POST con pago/condiciones bloqueado |
| Froiz | HTTP autenticado y localizado por tienda; fallback público no localizado | API Nuxt con huella completa, mutaciones sin reintento, relectura raw/procesada y fallback | HTTP por dirección/postal con fallback | No disponible por diseño: `orders/create` coloca la orden real |
| Eroski | HTML server-rendered público, no localizado; search/product | Lectura HTTP y escritura navegador verificada por HTTP | GET-only para la dirección ya seleccionada; otra dirección falla cerrado | No disponible por diseño: no existe frontera pre-pedido separada |

## Prueba pública viva

Una búsqueda de `arroz` con límite 3 devolvió tres productos válidos y con
precio positivo en las cuatro conexiones. Se usaron códigos postales genéricos
compatibles para Mercadona/Gadis y códigos aceptados por Froiz/Eroski.

Eroski fallaba inicialmente por interpretar cualquier enlace normal a
`/es/login/` como challenge. La detección ahora exige URL/formulario de login,
input de contraseña o CAPTCHA real, valida el host final y no afirma precisión
regional que el HTML público no demuestra. Eroski también anuncia ahora su
capacidad real de `product` y el lector de entrega GET-only.

## Evidencia autenticada local sanitizada

- Gadis: `local-captures/gadis-authenticated-strict-v2.json`, validada con 344
  eventos, 173 peticiones y 171 respuestas. Incluye lectura, alta, cantidad
  2→1, checkout observado y limpieza; el carrito original quedó restaurado y
  no hubo errores, warnings ni rutas de pedido/pago. El 24-08-2026 se repitió
  en vivo por HTTP el ciclo alta → cantidad 2 → cantidad 1 → retirada, con
  relectura tras cada escritura, confirmación de un solo uso y huella final
  exacta. La lectura de entrega devolvió 51 franjas, 43 disponibles, sin
  escribir.
- Eroski: `local-captures/eroski-delivery-authenticated.json`, 329 eventos, 236
  peticiones y 93 respuestas. Demuestra apertura de entrega, selección de
  recogida y franja. El lector integrado solo usa GET y nunca atribuye las
  franjas a una dirección distinta de la seleccionada. El 24-08-2026 se renovó
  la sesión, se verificó desde un proceso nuevo la persistencia de la rotación
  `JSESSIONID`, y se completó alta/retirada de un producto ordinario con cesta
  final idéntica. El GET de entrega detectó una dirección guardada ya
  redactada y su propia rotación se reutilizó en dos procesos nuevos
  consecutivos; no inventó franjas porque el contexto servidor aún no las
  rendía.
- Froiz: además de la captura anterior de 112 eventos (59 peticiones y 53
  respuestas), el 24-08-2026 se validó en vivo el backend HTTP autenticado:
  dirección y 36 franjas disponibles, cesta vacía, alta, cantidad 1→2→1,
  retirada, eliminación y retorno exacto a `cartId: null`. Todas las fases
  resultaron verdaderas, `channel_cart_untouched=true` y no se tocó pedido/pago.
- Mercadona: el 24-08-2026 se validaron login y sesión desde Chromium, carrito
  HTTP vacío, una dirección redactada, 60 franjas (8 disponibles) y un ciclo
  reversible alta/retirada con huella final exacta. El resultado marcó
  `ambiguous_write=false` y ninguna ruta de pedido/pago.

## Endurecimiento aplicado

- ninguna mutación se reintenta automáticamente tras `401` o respuesta ambigua;
  una limpieza solo se permite tras relecturas estables que demuestren la
  identidad y el estado exactos del probe, y nunca repite una retirada ambigua;
- Froiz solo acepta un bearer observado en `GET /api/me` cuando la respuesta
  2xx contiene la forma autenticada esperada;
- Gadis relee tras cada `update_product`, detiene el lote ante concurrencia y
  vuelve a comprobar versión e identidad antes del primer write de rollback;
  separa total de línea, precio unitario y costes no-producto antes de revisar
  presupuesto, total y restauración;
- Mercadona conserva `id`, `version` y el historial de `sources`, genera
  `+CA`/`-CA` como el storefront y valida overrides antes de escribir;
- todo commit vuelve a leer identidad, líneas, cantidades, precios y total;
- planes alterados, cantidades no finitas/negativas, duplicados, productos
  regulados y topes inseguros se rechazan antes de tocar al retailer;
- la reclamación del intento final en checkout navegador es atómica dentro del
  proceso, por lo que dos llamadas concurrentes no pueden pulsar dos veces;
- URLs privadas, cookies, tokens, direcciones y demás valores personales no se
  devuelven ni se incorporan a fixtures;
- el capturador bloquea rutas de pedido/pago y, en una sonda de envío, todo
  non-GET antes de que salga de Chromium.

## Límites deliberados ya clasificados

- Froiz y Eroski no exponen checkout/pedido: no es una tarea por completar,
  sino la consecuencia de que el primer endpoint observado ya coloca la orden.
- Eroski no selecciona otra dirección/tienda desde una lectura. Su `delivery`
  GET-only funciona para el contexto actual y falla cerrado para otro.
- El pedido final de Mercadona/Gadis permanece experimental, separado y
  deshabilitado por defecto. No se prueba durante desarrollo o auditoría.
- Gadis no dispone de optimistic locking en el endpoint `update_product`; se
  minimiza la ventana con relecturas por mutación, pero una edición externa en
  el instante exacto entre lectura y write no puede eliminarse sin soporte del
  retailer.
- Una sesión caducada se clasifica como estado operativo local: el código no la
  disfraza como conexión verificada y requiere un nuevo login del propietario.

## Cierre de automatización práctica (25-08-2026)

Las cuatro conexiones anuncian ahora `human_handoff`. La misma herramienta
revalida la última frontera segura y abre Chromium sin clics automatizados:

- Mercadona y Gadis: checkout, total, dirección y franja verificados antes de
  abrir la revisión final;
- Froiz y Eroski: cesta verificada y, cuando se proporciona, entrega verificada,
  deteniéndose antes de la frontera que puede crear el pedido real.

Se corrigió además la continuidad del checkout HTTP de Gadis: el snapshot
mínimo queda en almacenamiento local protegido, sobrevive reinicios, vuelve a
compararse con productos, cantidades, precios y total, y nunca cae en el método
de pedido del backend navegador. Un fallo al persistir después de crear el
checkout se clasifica explícitamente como no reintentable. El verificador local
de entrega pasa ahora el consentimiento requerido por el contrato de checkout,
sin llamar a pedido ni pago.

La suite de cierre contiene 550 pruebas y pasan también `compileall`, Ruff,
`pip check` y la construcción del wheel 0.5.0. La comprobación local final fue
solo de lectura. Mercadona, Gadis y Eroski validaron sesión viva; Froiz detectó
correctamente su sesión HTTP caducada y el login visible expiró sin completarse,
sin mutar carrito ni alcanzar checkout, pedido o pago.

Durante la auditoría no llegó al retailer ninguna petición de pedido o pago y
no se mostraron, copiaron ni persistieron secretos en el repositorio.

## Sonda autorizada hasta la frontera de pago (25-08-2026)

El propietario autorizó expresamente avanzar en las cuentas de prueba de
Mercadona, Gadis y Eroski hasta la pantalla previa a introducir tarjeta. Froiz
quedó excluido porque esa cuenta sí tiene historial de un pedido real. La
autorización no cambió la frontera de seguridad: ningún control final de compra,
pedido, pago, Redsys o 3-D Secure podía accionarse.

- Mercadona: la sesión, cesta, una dirección, 60 franjas (9 disponibles) y un
  ciclo alta/retirada volvieron a validarse. Se hizo exactamente un intento de
  crear checkout con un producto ordinario de menos de 5 EUR. El proveedor
  devolvió `ProviderError` antes de obtener un checkout autoritativo. Como el
  resultado remoto de un POST puede ser ambiguo, no se reintentó ni se intentó
  seleccionar entrega. Dos relecturas estables permitieron retirar solo el
  producto de prueba y confirmar la restauración de la cesta vacía. Una sonda
  posterior de Chromium bloqueó 21 peticiones non-GET y no reveló checkout ni
  formulario de tarjeta por GET.
- Gadis: la sesión y la cesta HTTP siguieron autenticadas. La cuenta no exponía
  ninguna dirección guardada; por ello el verificador se detuvo antes de escribir
  franja o crear checkout. Chromium abrió la cesta, bloqueó 14 peticiones
  non-GET y no encontró un enlace seguro de checkout. No se inventó ni copió una
  dirección de otra tienda. El propietario añadió después una dirección desde
  una ventana local protegida, que bloqueó 10 peticiones de checkout/pedido/pago;
  la relectura HTTP confirmó una dirección utilizable. Se realizó entonces un
  único intento de checkout: la franja se aplicó y retiró correctamente y la
  cesta quedó restaurada, pero la creación no produjo un checkout verificable.
  No se reintentó. El `GET /api/config/checkout` respondió `405`, por lo que el
  posible resultado remoto del POST tampoco puede enumerarse de forma segura.
- Eroski: la sesión, la cesta existente y una dirección redactada siguieron
  accesibles. Con todas las peticiones non-GET bloqueadas desde antes de la
  primera navegación, se abrió la cesta pero no apareció un enlace de checkout
  separado del posible envío del pedido. Se bloquearon 4 peticiones non-GET y
  no se accionó texto ambiguo como "tramitar pedido".
- Froiz: no se realizó ninguna sonda profunda ni mutación.

Gadis sí alcanzó posteriormente la pantalla de tarjeta mediante el contrato
reversible `updateCart`/`summaryCheckout`, restaurando dirección, propietario,
postal, tipo, comentarios, fecha y franja. Mercadona no repitió su POST ambiguo
y Eroski, tras renovar login, no recibió franjas para la dirección actual.
No llegó al retailer ninguna petición de pedido o pago. El diagnóstico HTTP de Mercadona redacta ahora
los identificadores privados de ruta y conserva solo la operación y el código
de estado seguro; esto permitirá clasificar una futura incidencia sin repetir a
ciegas una escritura ambigua. Gadis aplica la misma redacción y su verificador
conserva el fallo primario aunque después ejecute la limpieza de la franja.

## Revisión visible protegida (25-08-2026)

Se añadió una guarda común a `open_human_review`: antes de navegar instala una
ruta Chromium que deja pasar solo GET, aborta cualquier otro método y exige que
la URL final conserve la ruta de checkout solicitada. Una redirección a login,
portada o carrito ya no cuenta como checkout alcanzado.

- Gadis: `/pag/proceso-de-compra/compra-segura` quedó verificada como ruta final.
  El resumen reversible se preparó, se bloqueó una petición non-GET y el contexto
  completo de entrega se restauró mediante doble lectura estable.
- Froiz: la ruta observada de checkout quedó verificada con un carrito
  desechable activo. Se bloquearon ocho non-GET; después se retiró el producto,
  se borró el carrito desechable y el canal original quedó intacto. No se llamó
  `orders/create`, que continúa bloqueado por colocar el pedido real.
- Mercadona: se hizo un único intento con un producto ordinario de menos de
  5 EUR. La creación volvió a ser ambigua, no se reintentó y el producto se
  retiró con la cesta vacía restaurada. No existe todavía un checkout
  autoritativo que pueda entregarse a la revisión visible.
- Eroski: se renovó la sesión y se leyó una dirección, pero el servidor no
  ofreció franjas. Las rutas GET configuradas de checkout no produjeron una
  revisión válida. El formulario final no se envió.

Ninguna de estas sondas alcanzó un endpoint de pedido o pago.
