# Verificación local del carrito HTTP de Gadis

Esta comprobación debe ejecutarla un agente en la máquina que conserva la sesión de Gadis. Es completamente de solo lectura: no añade productos, no cambia cantidades, no abre un checkout y no intenta realizar pedidos o pagos.

## Preparación

```powershell
git fetch origin
git switch feat/initial-mcp
git pull --ff-only origin feat/initial-mcp

.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
```

## Ejecución

```powershell
python .\tools\verify_gadis_http_local.py
```

El resultado correcto tiene esta forma:

```json
{
  "ok": true,
  "account": {
    "authenticated": true,
    "validated_live": true,
    "bearer_token_available": true,
    "account_backend": "gadis_http"
  },
  "cart": {
    "version_present": true,
    "cart_backend": "gadis_http",
    "browser_driven": false
  },
  "retailer_write_performed": false,
  "order_or_payment_attempted": false
}
```

El script no imprime nombres de productos, identificadores de líneas, cookies, tokens, direcciones ni datos del perfil.

## Obligaciones del agente

Si `ok` es `false`, el agente debe:

1. comprobar que está usando la sesión local correcta;
2. revisar el motivo sin mostrar ni copiar el contenido de `storage_state.json`;
3. corregir el cliente o la resolución de sesión cuando sea un fallo de código;
4. repetir la prueba;
5. no aceptar un fallback silencioso a Playwright como verificación del cliente HTTP;
6. no pedir al propietario que repita la captura completa.

Solo puede solicitar intervención humana para CAPTCHA, 2FA o desbloqueo del gestor de contraseñas.

## Prompt listo para el agente

```text
Lee AGENTS.md y trabaja en la rama feat/initial-mcp. Actualiza la rama y ejecuta
python .\tools\verify_gadis_http_local.py en la máquina que contiene la sesión
de Gadis. Esta prueba es de solo lectura. No modifiques el carrito, no abras un
checkout y no intentes ningún pedido o pago.

Si la salida no tiene ok=true y cart_backend=gadis_http, diagnostica y corrige
el problema tú mismo. No muestres storage_state, cookies o tokens y no me pidas
que repita la captura. Ejecuta después pytest, compileall y ruff, y reporta el
resultado con confirmación expresa de que no hubo escrituras ni intento de pago.
```
