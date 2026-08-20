# Proveedores controlados por navegador

Gadis y Froiz conservan sus lecturas de catálogo estructuradas, pero ejecutan las
operaciones autenticadas sobre la interfaz visible de la tienda mediante
Playwright. Esto evita inventar o acoplar el proyecto a endpoints privados de
escritura que pueden cambiar sin aviso.

## Sesión

`login_with_browser` abre Chromium o Chrome en modo visible. El usuario inicia
sesión directamente en la tienda y pulsa **Open Grocery: guardar sesión**. El MCP
guarda un `storage_state.json` local con permisos restringidos; contraseñas,
códigos de verificación, cookies y tokens no se aceptan como argumentos MCP.

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
carrito. Después de escribir se comprueban de nuevo líneas, cantidades y total.
Un total no verificable en una cesta no vacía se trata como error y se intenta
restaurar el estado anterior.

Las operaciones siguen el flujo común de dos fases:

1. `prepare_*` obtiene el estado actual y emite una frase exacta.
2. El usuario revisa el resumen.
3. `commit_*` consume una confirmación de un solo uso y vuelve a comprobar el
   estado antes de actuar.

## Pedido definitivo

El clic final de un proveedor de navegador necesita todas estas barreras:

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

La implementación está completa como backend de navegador. La compatibilidad en
vivo debe validarse al menos una vez con la cuenta del propietario porque la
interfaz de cada tienda puede cambiar; un selector ausente provoca un fallo
cerrado, nunca un éxito supuesto.
