# Proveedores controlados por navegador

Playwright permanece como backend de login y fallback para operaciones que un
contrato HTTP no puede representar con seguridad. Gadis y Froiz prefieren sus
clientes HTTP autenticados; Eroski usa HTTP para leer, navegador para las
escrituras de carrito verificadas y GET-only para el contexto de entrega ya
seleccionado.

## Sesión

`login_with_browser` abre Chromium o Chrome en modo visible. El usuario inicia
sesión directamente en la tienda. Froiz y Eroski abren su entrada de acceso y
guardan automáticamente solo después de detectar un control exacto de
logout/desconexión; Mercadona exige además lecturas 2xx de cliente y carrito.
El control auxiliar heredado se conserva donde no hay una prueba automática
específica. El MCP guarda un `storage_state.json` local con permisos
restringidos; contraseñas, códigos de verificación, cookies y tokens no se
aceptan como argumentos MCP.

También se puede importar un `storage_state.json` existente. El archivo se
valida, se limita a 5 MiB y debe contener cookies u orígenes pertenecientes al
dominio del supermercado.

## Lectura resiliente

El driver escucha las respuestas JSON relacionadas con cesta, dirección,
entrega y checkout. Cuando existe una respuesta estructurada la normaliza; si no,
recurre al DOM renderizado. Los selectores priorizan roles y nombres accesibles y
tienen alternativas estructurales para líneas, cantidades, direcciones y
franjas.

Las URLs se limpian antes de devolverlas. Los parámetros privados de un checkout
se conservan únicamente en un archivo local protegido y nunca aparecen en una
respuesta MCP.

## Escrituras

El plan de carrito contiene el conjunto exacto de productos y cantidades
revisados. Antes de escribir se vuelve a leer una huella determinista del
carrito. Después de escribir se comprueban de nuevo líneas, cantidades, precios
y total.
Un total no verificable en una cesta no vacía se trata como error. Primero se
relee la cesta para diagnosticar una respuesta ambigua; solo después se intenta
restaurar el estado anterior, y el rollback también debe quedar verificado.

Las operaciones siguen el flujo común de dos fases:

1. `prepare_*` obtiene el estado actual y emite una frase exacta.
2. El usuario revisa el resumen.
3. `commit_*` consume una confirmación de un solo uso y vuelve a comprobar el
   estado antes de actuar.

## Pedido definitivo

El clic final de un proveedor de navegador que anuncie esa capacidad necesita
todas estas barreras:

```text
OPEN_GROCERY_ENABLE_RETAILER_WRITES=1
OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=1
OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=1
OPEN_GROCERY_ORDER_APPROVAL_CODE=<secreto local>
```

El intento se registra localmente **antes** del clic. Ante un fallo o resultado
ambiguo, el MCP bloquea cualquier reintento automático y obliga a revisar el
historial de pedidos de la tienda para evitar duplicados. Los desafíos bancarios,
PSD2, 3-D Secure, Bizum, SMS o biometría permanecen siempre en manos del usuario.

## Verificación

Las pruebas automatizadas cubren normalización, aislamiento de sesión, límites,
control de concurrencia, rollback, protección de URLs, rechazo de productos
restringidos y política de no reintento. No realizan compras reales.

La compatibilidad en vivo debe validarse con la cuenta del propietario porque la
interfaz puede cambiar; un selector ausente provoca un fallo cerrado. Froiz y
Eroski no anuncian checkout/order: en ambos casos la frontera observada puede
crear directamente un pedido real.
