# Delegar la parte local a un agente

La mayor parte del proyecto puede desarrollarse sin acceso a tu ordenador. La excepción son las pruebas que dependen de tu conexión, navegador y sesión autenticada. Esta guía conserva ejemplos de Gadis/Froiz, pero el capturador también admite Mercadona y Eroski. Estas instrucciones permiten delegar esa parte a un agente local con acceso a terminal y, preferiblemente, automatización de navegador.

## Qué agente sirve

El agente debe poder trabajar dentro del repositorio y ejecutar comandos. Para encargarse también de los clics debe disponer de alguna forma de automatización local del navegador o del escritorio, por ejemplo Playwright, browser-use, Computer Use o una herramienta equivalente.

Un agente únicamente conversacional, sin terminal ni navegador, no podrá realizar la captura por sí mismo.

## Preparación mínima

Abre el repositorio en el agente y asegúrate de que el directorio de trabajo sea la raíz de `open-grocery-mcp`. No le pegues contraseñas, cookies ni tokens en el chat.

El agente leerá [`AGENTS.md`](../AGENTS.md), que contiene las reglas operativas y de seguridad permanentes.

## Prompt listo para pegar

Copia este mensaje en tu agente local:

```text
Trabaja directamente en este repositorio y lee AGENTS.md antes de hacer nada.

Tu objetivo es completar la parte local y autenticada de la integración de Gadis sin pedirme que repita manualmente una lista de fases. Tienes que:

1. comprobar la rama feat/initial-mcp, el estado de Git y las pruebas actuales;
2. inspeccionar si ya existe una sesión local en ~/.open-grocery-mcp/gadis/storage_state.json, sin mostrar ni copiar su contenido;
3. ejecutar una captura autenticada y visible de Gadis usando la sesión existente, variables de entorno locales o automatización de navegador;
4. realizar tú las acciones seguras necesarias: leer cesta, añadir un producto ordinario de prueba, cambiar 1→2→1, eliminarlo, consultar direcciones/franjas y abrir checkout;
5. no enviar nunca un pedido, no iniciar ningún pago y bloquear toda escritura en la fase de sonda final;
6. validar el JSON con tools/validate_capture.py y no aceptar como éxito una captura con events=0;
7. si la captura falla, diagnosticar y corregir la instrumentación o los selectores, añadir pruebas y repetir el flujo mínimo; no limitarte a decirme que vuelva a hacerlo yo;
8. extraer el contrato HTTP sanitizado, implementar todo lo verificable del cliente ligero de Gadis y conservar el fallback de navegador para lo no verificado;
9. ejecutar pytest, compileall y ruff;
10. entregarme un informe con eventos/requests/responses, fases verificadas, archivos modificados, pruebas, limitaciones y confirmación de que ningún pedido o pago llegó al supermercado.

No imprimas secretos ni abras archivos de sesión en el chat. Solo puedes pedirme intervenir si aparece CAPTCHA, 2FA o hay que desbloquear el gestor de contraseñas. En ese caso, espera a que complete únicamente ese paso y continúa tú con todo lo demás.
```

Para Froiz basta con sustituir `Gadis` y `gadis` por `Froiz` y `froiz`.

## Cierre local de Mercadona

Mercadona puede comprobarse sin navegador si ya existe una sesión local válida:

```powershell
Remove-Item Env:OPEN_GROCERY_ENABLE_ORDER_SUBMISSION -ErrorAction SilentlyContinue
Remove-Item Env:OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION -ErrorAction SilentlyContinue
python .\tools\verify_mercadona_local.py
```

Este recorrido solo lee sesión, carrito, direcciones y franjas. Debe terminar
con `ok: true`, `retailer_write_performed: false` y
`order_or_payment_attempted: false`. Nunca crea checkout ni llama a pedido,
pago, Redsys o 3-D Secure.

La prueba reversible es opcional y requiere dos consentimientos locales: el
argumento `--allow-reversible-cart-write` y
`OPEN_GROCERY_ENABLE_RETAILER_WRITES=1`. Añade un producto ordinario ausente
por un máximo temporal de 5 EUR, relee el carrito y restaura exactamente las
líneas y el total originales. Una respuesta ambigua o una restauración no
verificable detiene el proceso: no se reintenta ni se ejecuta limpieza
automática. Los opt-ins de pedido deben permanecer desactivados.

Solo el propietario puede habilitar esta comprobación reversible en su proceso
local:

```powershell
$env:OPEN_GROCERY_ENABLE_RETAILER_WRITES = "1"
python .\tools\verify_mercadona_local.py --allow-reversible-cart-write
Remove-Item Env:OPEN_GROCERY_ENABLE_RETAILER_WRITES -ErrorAction SilentlyContinue
```

La única parte humana es iniciar sesión en una ventana visible de Mercadona si
no hay `~/.open-grocery-mcp/mercadona/storage_state.json` reutilizable: el
propietario introduce allí la contraseña y completa CAPTCHA/2FA si aparecen.
El agente no debe recibir esos valores; tras esa intervención continúa con la
lectura y el verificador automáticamente.

## Comandos que debería ejecutar el agente

Configuración inicial en Windows PowerShell:

```powershell
git fetch origin
git switch feat/initial-mcp
git pull --ff-only origin feat/initial-mcp

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    py -3.11 -m venv .venv
}
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
python -m playwright install chromium
pytest
```

Captura automatizada visible, cuando la cuenta de prueba ya está disponible localmente:

```powershell
$env:OPEN_GROCERY_CAPTURE_HEADLESS = "0"
python .\tools\capture_http_contract.py `
  --store gadis `
  --mode authenticated `
  --output .\local-captures\gadis-authenticated.json
```

Captura conducida por el agente desde un navegador visible:

```powershell
python .\capture_http_local.py `
  --store gadis `
  --output .\local-captures\gadis-authenticated.json
```

Validación obligatoria:

```powershell
python .\tools\validate_capture.py `
  .\local-captures\gadis-authenticated.json `
  --minimum-events 5 `
  --require-response
```

La salida debe contener `"ok": true`. Una salida con `events: 0` obliga al agente a depurar y repetir; no es una captura parcial útil.

## Cuándo debes intervenir tú

La intervención humana debería limitarse a uno de estos casos:

- desbloquear el gestor de contraseñas;
- completar CAPTCHA;
- introducir un código 2FA/SMS;
- confirmar que se puede usar una cuenta de pruebas;
- revisar el carrito antes de cualquier futuro pedido real.

No deberías tener que:

- ir marcando once fases;
- copiar peticiones de DevTools;
- interpretar JSON o logs;
- buscar endpoints;
- cambiar selectores;
- repetir el flujo entero porque el capturador falló;
- programar el adaptador.

## Qué debe entregarte el agente

Un resultado válido incluye:

```text
Captura: local-captures/gadis-authenticated.json
Eventos: <número mayor que 0>
Requests: <número mayor que 0>
Responses: <número mayor que 0>
Fases verificadas: ...
Pedido enviado: NO
Pago iniciado: NO
Tests: ...
Limitaciones: ...
```

Los archivos de `local-captures/` y `~/.open-grocery-mcp/` nunca deben subirse al repositorio. El agente puede utilizar el contrato sanitizado para crear fixtures sin valores privados.
