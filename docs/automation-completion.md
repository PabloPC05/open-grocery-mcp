# Criterio de finalización de la automatización

Fecha de cierre: 25-08-2026.

Este documento distingue una tarea pendiente de un límite deliberado del
retailer o de seguridad. El MCP se considera terminado en sentido práctico
cuando puede buscar y comparar el surtido, preparar una cesta autenticada,
revalidar la última frontera segura y abrir la revisión humana sin enviar un
pedido ni iniciar un pago.

## Checklist global

- [x] Búsqueda paginada o cobertura declarada sin inventar exhaustividad.
- [x] Equivalencias, sustituciones, ofertas y cesta multi-supermercado con
  diagnóstico de incertidumbre.
- [x] Carrito autenticado con prepare/confirm/commit, tope, concurrencia,
  relectura y rollback seguro.
- [x] Direcciones redactadas y franjas revalidadas según el contrato disponible.
- [x] Checkout separado del pedido en Mercadona y Gadis.
- [x] Frontera cart-only explícita en Froiz y Eroski, sin simular checkout.
- [x] `prepare_human_handoff` común para revalidar total, cesta y entrega.
- [x] `open_human_review` visible, con navegación GET y cero clics automatizados.
- [x] Resumen HTTP reversible de Gadis persistente, releíble tras reinicio y
  aislado del POST que contiene pago y condiciones.
- [x] Pedido/pago separado, apagado por defecto y ausente de las verificaciones.
- [x] Pruebas, compilación y lint ejecutados sobre el árbol final.

## Última frontera por tienda

| Tienda | Frontera automática | Entrega | Traspaso humano |
|---|---|---|---|
| Mercadona | Checkout HTTP autoritativo implementado; el último POST vivo quedó ambiguo y no se repitió | Dirección y franja HTTP verificadas | Revisión de checkout cuando su creación queda confirmada |
| Gadis | Resumen HTTP reversible verificado; el POST con pago/condiciones está bloqueado | Dirección/fecha/franja y restauración completa verificadas | Pantalla de tarjeta alcanzada por GET tras preparar el resumen |
| Froiz | Cesta HTTP autoritativa | Dirección y calendario HTTP | Cesta verificada; checkout manual |
| Eroski | Cesta HTTP + mutación navegador verificada | GET-only del contexto seleccionado | Cesta verificada; checkout manual |

## Estados que no son trabajo pendiente

- El clic final, Redsys, 3-D Secure, confirmación bancaria y pago pertenecen a
  la persona y no forman parte de la automatización segura.
- Froiz y Eroski no ganan una capacidad `checkout` hasta que exista evidencia
  de una operación previa al pedido real. Forzarla sería una regresión.
- Una sesión caducada necesita login humano; el MCP debe detectarlo y fallar
  cerrado, no eludir CAPTCHA o 2FA.
- Cambios futuros en contratos privados de los retailers son mantenimiento de
  compatibilidad, no una carencia actual que pueda darse por resuelta sin nueva
  evidencia local.

## Evidencia

La evidencia viva sanitizada y los límites por conexión están en
[`connection-audit.md`](connection-audit.md). El cierre semántico P0-P2 está en
[`equivalence-roadmap.md`](equivalence-roadmap.md). Ninguna prueba de cierre
envía pedidos ni inicia pagos.

Validación del árbol de cierre:

- `pytest -q`: 550 pruebas correctas.
- `python -m compileall -q src tests tools`: correcto.
- `ruff check .`: correcto.
- `python -m pip check`: dependencias consistentes.
- wheel `open_grocery_mcp-0.5.0-py3-none-any.whl`: construido correctamente
  en un directorio temporal.

La lectura local final confirmó Mercadona, Gadis y Eroski autenticados en vivo.
La sesión HTTP de Froiz estaba caducada; se abrió el login visible durante cinco
minutos y no fue completado. Esto se conserva como estado local de cuenta, no
como capacidad implementativa pendiente: el adaptador lo detectó, no escribió
y no afirmó estar autenticado.

La sonda posterior autorizada alcanzó la pantalla de tarjeta de Gadis mediante
`PUT /api/config/updateCart` con `summaryCheckout=true`; el contexto completo
se restauró y `/api/config/checkout` quedó bloqueado por contener pago y
condiciones. En Mercadona el único POST anterior sigue siendo ambiguo y no se
repitió. En Eroski se renovó la sesión y se verificó la carga Tapestry de la
dirección guardada, pero el servidor no ofreció ninguna franja actual, por lo
que no se avanzó a resumen. Froiz se excluyó expresamente.
Estos resultados no habilitan nuevos endpoints ni rebajan el cierre seguro;
quedan detallados en `connection-audit.md`.

## Verificadores de revisión visible

Las revisiones locales protegidas pueden repetirse con los opt-ins de pedido
apagados:

```powershell
$env:OPEN_GROCERY_ENABLE_RETAILER_WRITES = "1"
$env:OPEN_GROCERY_ENABLE_ORDER_SUBMISSION = "0"
$env:OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION = "0"
python .\tools\verify_gadis_checkout_review_local.py `
  --max-total 10 --timeout-seconds 60
python .\tools\verify_froiz_cart_local.py `
  --allow-reversible-cart-write --max-added-value 5 `
  --open-checkout-review --review-timeout-seconds 60
```

Ambos comandos bloquean todo non-GET en la ventana visible y exigen una ruta
final de checkout exacta. El primero restaura el contexto de entrega; el segundo
elimina el carrito desechable. Mercadona dispone de
`tools/verify_mercadona_checkout_review_local.py`, pero un resultado ambiguo de
creación se considera terminal y nunca se reintenta. Eroski conserva la frontera
de entrega hasta que el servidor ofrezca una franja válida.
