# Verificación local del carrito HTTP de Gadis

Estas pruebas deben ejecutarlas un agente en la máquina que conserva la sesión de Gadis. No muestran cookies, tokens, nombres de productos, identificadores privados, direcciones ni datos del perfil.

## Preparación

```powershell
git fetch origin
git switch feat/initial-mcp
git pull --ff-only origin feat/initial-mcp

.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
```

## 1. Lectura HTTP

Esta comprobación es completamente de solo lectura:

```powershell
python .\tools\verify_gadis_http_local.py
```

Debe devolver `ok=true`, `account_backend="gadis_http"`, `cart_backend="gadis_http"`, `browser_driven=false`, `retailer_write_performed=false` y `order_or_payment_attempted=false`.

## 2. Mutación reversible

Esta prueba necesita dos autorizaciones simultáneas: la variable local de escrituras y un flag explícito en el comando. Rechaza la ejecución si están activadas variables de envío de pedidos.

```powershell
$env:OPEN_GROCERY_ENABLE_RETAILER_WRITES = "1"
Remove-Item Env:OPEN_GROCERY_ENABLE_ORDER_SUBMISSION -ErrorAction SilentlyContinue
Remove-Item Env:OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION -ErrorAction SilentlyContinue

python .\tools\verify_gadis_cart_mutation_local.py `
  --allow-reversible-cart-write `
  --max-added-value 5.00
```

La prueba:

1. lee y conserva en memoria la firma del carrito inicial;
2. exige el backend HTTP, sin fallback silencioso a Playwright;
3. selecciona un producto ordinario ausente de la cesta;
4. limita a 5 EUR el valor temporal máximo y también el pico de cantidad 2;
5. ejecuta `añadir → 1 → 2 → 1 → eliminar`;
6. utiliza borrador, preparación, frase exacta y confirmación de un solo uso;
7. vuelve a leer el carrito después de cada escritura;
8. si una fase falla, lee primero y elimina únicamente el producto de prueba si sigue presente;
9. compara líneas, cantidades, total y número de productos con el estado inicial.

El resultado válido incluye:

```json
{
  "ok": true,
  "backend": "gadis_http",
  "confirmation_single_use": true,
  "cart_restored": true,
  "initial_and_final_signature_match": true,
  "initial_and_final_total_match": true,
  "initial_and_final_count_match": true,
  "order_or_payment_attempted": false,
  "secrets_exposed": false
}
```

Una salida con `cart_restored=false` es un fallo crítico. El agente debe detener nuevas escrituras, comprobar el carrito de forma segura y no reintentar operaciones ambiguas a ciegas.

Al terminar la sesión de pruebas puede desactivar las escrituras:

```powershell
Remove-Item Env:OPEN_GROCERY_ENABLE_RETAILER_WRITES -ErrorAction SilentlyContinue
```

## 3. Direcciones, franjas y resumen seguro de checkout por HTTP

Lecturas, escritura reversible de franja y preparación del contexto que usa la
página GET de checkout. El endpoint `/api/config/checkout` contiene campos de
pago y aceptación de condiciones, por lo que está bloqueado antes de la red:

```powershell
$env:OPEN_GROCERY_ENABLE_RETAILER_WRITES = "1"
Remove-Item Env:OPEN_GROCERY_ENABLE_ORDER_SUBMISSION -ErrorAction SilentlyContinue
Remove-Item Env:OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION -ErrorAction SilentlyContinue

python .\tools\verify_gadis_delivery_local.py --allow-reversible-schedule-write

# prepara y restaura el resumen que permite abrir la pantalla de tarjeta:
python .\tools\verify_gadis_delivery_local.py `
  --allow-reversible-schedule-write `
  --allow-checkout-summary
```

Resultado válido: `calendar_read`, `addresses_read`, `schedule_applied`,
`schedule_removed`, `checkout_summary_prepared` y `state_restored=true`.
`checkout_created=false` y `order_or_payment_attempted=false` son garantías
deliberadas: esta prueba nunca llama al POST que contiene pago/condiciones.

Si los microservicios devuelven `AuthenticationRequired`, el bearer Keycloak
de la sesión está caducado: pide al propietario un `login_with_browser`
visible y repite. El método completo de migración a HTTP está en
`docs/http-backend-playbook.md`.

## Obligaciones del agente

Si cualquiera de las pruebas falla, el agente debe diagnosticar y corregir el cliente, el workflow o el script sin pedir al propietario que repita la captura completa. Solo puede solicitar intervención humana para CAPTCHA, 2FA o desbloqueo del gestor de contraseñas.

Después debe ejecutar:

```powershell
pytest
python -m compileall -q src tests tools
ruff check .
```

## Prompt breve para el agente

```text
Lee AGENTS.md y trabaja en feat/initial-mcp. Actualiza la rama y ejecuta primero
python .\tools\verify_gadis_http_local.py. Después ejecuta la prueba reversible
siguiendo docs/gadis-http-local-verification.md, con un máximo temporal de 5 EUR.

No abras checkout, no envíes pedidos, no inicies pagos y no muestres storage_state,
cookies o tokens. Si algo falla, lee el carrito antes de cualquier limpieza y
restaura únicamente el producto de prueba. No aceptes el resultado hasta que
ok=true y cart_restored=true. Ejecuta luego pytest, compileall y ruff, haz push
y actualiza la PR con los resultados exactos.
```
