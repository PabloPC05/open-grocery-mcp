# Open Grocery MCP

Servidor [Model Context Protocol](https://modelcontextprotocol.io/) para consultar supermercados mediante una interfaz común, comparar una misma cesta y preparar borradores revisables antes de tocar una tienda real.

> **Estado: alpha (`0.1.0`).** La versión inicial es deliberadamente de solo lectura frente a los supermercados. No inicia sesión, no modifica carritos reales, no confirma pedidos y no procesa pagos.

## Qué funciona ahora

- Catálogo de **Gadis**: búsqueda, detalle y categorías.
- Catálogo de **Mercadona**: búsqueda, detalle y categorías con almacén resuelto desde el código postal.
- Comparación de una cesta entre supermercados con precios normalizados.
- Coincidencia explicable entre la petición y los productos encontrados.
- Cantidades, límites máximos por unidad y artículos opcionales.
- Borradores de carrito locales con caducidad y confirmación humana obligatoria.
- Transporte MCP local por `stdio` y remoto por Streamable HTTP.
- Arquitectura de proveedores extensible; Froiz es el siguiente adaptador previsto.

## Herramientas MCP

| Herramienta | Función | Escribe en la tienda |
|---|---|---:|
| `health` | Estado, versión y modo de seguridad | No |
| `stores` | Tiendas, idiomas, capacidades y requisitos de ubicación | No |
| `search_products` | Busca productos en una tienda | No |
| `get_product` | Obtiene el detalle de un producto | No |
| `list_categories` | Devuelve el árbol de categorías | No |
| `compare_basket` | Compara una cesta entre varias tiendas | No |
| `prepare_cart` | Crea un borrador local con IDs y subtotal | No |
| `get_cart_draft` | Recupera un borrador local | No |
| `delete_cart_draft` | Elimina un borrador local | No |

Ejemplo de cesta:

```json
{
  "postal_code": "28050",
  "stores": ["gadis", "mercadona"],
  "items": [
    {"query": "leche entera 1 L", "quantity": 2},
    {"query": "huevos camperos 12 unidades", "quantity": 1},
    {"query": "arroz redondo 1 kg", "quantity": 1, "max_unit_price": 2.5}
  ]
}
```

La comparación excluye, hasta que cada proveedor lo implemente de forma verificable, gastos de envío, pedido mínimo, cupones personales, descuentos de fidelización y sustituciones del checkout.

## Instalación para desarrollo

Requiere Python 3.11 o posterior.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pytest
```

Con `uv`:

```bash
uv sync --extra dev
uv run pytest
uv run open-grocery-mcp
```

## Uso local por `stdio`

El comando predeterminado ejecuta el servidor por entrada/salida estándar:

```bash
open-grocery-mcp
```

Configuración genérica de un cliente MCP:

```json
{
  "mcpServers": {
    "open-grocery": {
      "command": "/ruta/al/entorno/bin/open-grocery-mcp"
    }
  }
}
```

También puede ejecutarse desde el código fuente:

```json
{
  "mcpServers": {
    "open-grocery": {
      "command": "python",
      "args": ["-m", "open_grocery_mcp"]
    }
  }
}
```

## Uso por HTTP

```bash
open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

El endpoint MCP se publica en `/mcp`. El valor predeterminado escucha únicamente en `127.0.0.1`.

**No expongas la modalidad HTTP directamente a Internet.** Esta primera versión no incorpora autenticación de servidor. Para acceso remoto utiliza una red privada como Tailscale o un proxy inverso con TLS, autenticación, límites de uso y registro de auditoría.

## Configuración por variables de entorno

| Variable | Uso |
|---|---|
| `OPEN_GROCERY_TRANSPORT` | `stdio` o `streamable-http` |
| `OPEN_GROCERY_HOST` | Host HTTP; por defecto `127.0.0.1` |
| `OPEN_GROCERY_PORT` | Puerto HTTP; por defecto `8000` |
| `OPEN_GROCERY_GADIS_STORE` | Fuerza el identificador de surtido de Gadis |
| `OPEN_GROCERY_MERCADONA_WAREHOUSE` | Fuerza un almacén de Mercadona cuando no se pasa código postal |
| `OPEN_GROCERY_MERCADONA_ALGOLIA_APP` | Sobrescribe la aplicación pública de búsqueda |
| `OPEN_GROCERY_MERCADONA_ALGOLIA_KEY` | Sobrescribe la clave pública de búsqueda |

Para Mercadona se recomienda pasar siempre `postal_code`. El servidor consulta el mismo cambio de código postal usado por la tienda y obtiene el almacén que sirve esa zona. Esto evita comparar un surtido de Madrid con precios de otro almacén.

Gadis publica un surtido predeterminado. La resolución automática de un surtido concreto desde el código postal todavía no está implementada; el resultado indica el `store_id` utilizado y puede fijarse mediante variable de entorno.

## Modelo de seguridad

La arquitectura separa expresamente tres niveles:

1. **Catálogo:** búsqueda y lectura pública.
2. **Borrador:** selección local de productos, cantidades y subtotal.
3. **Compra:** autenticación, carrito remoto, entrega, checkout y pago.

La versión `0.1.0` implementa únicamente los dos primeros niveles. Un futuro proveedor de carrito deberá ser una capacidad opcional, mantener sesiones por usuario, exigir una confirmación explícita y no podrá reutilizar automáticamente una herramienta de catálogo para colocar pedidos.

## Añadir un supermercado

Cada adaptador hereda de `GroceryProvider` e implementa como mínimo:

```python
class ExampleProvider(GroceryProvider):
    info = StoreInfo(...)

    def search(self, query, *, limit=10, postal_code=None, eco=False):
        ...
```

`product()` y `categories()` son opcionales. Las capacidades reales se declaran en `StoreInfo`; no se anuncian herramientas que el proveedor no pueda verificar. Consulta [`docs/provider-contract.md`](docs/provider-contract.md).

## Hoja de ruta

1. Validación en vivo de Gadis y Mercadona en varios códigos postales.
2. Adaptador de **Froiz** para catálogo y comparación.
3. Eroski/Familia, Carrefour, DIA y Alcampo.
4. Gastos de entrega, pedido mínimo y promociones no personalizadas.
5. Proveedores opcionales de carrito con sesiones locales cifradas.
6. Vista previa de checkout y selección de franja.
7. Confirmación de pedido separada, desactivada por defecto y siempre humana.

No se automatizará el pago bancario ni la autenticación reforzada.

## Pruebas

Las pruebas de proveedores usan transportes HTTP simulados: no generan pedidos ni dependen de una cuenta real.

```bash
pytest
python -m compileall -q src
```

## Procedencia y licencia

Open Grocery MCP se publica bajo licencia MIT. La arquitectura y parte del conocimiento de los endpoints se inspiran en el proyecto MIT [`jgalea/grocery-cli`](https://github.com/jgalea/grocery-cli); se conserva la atribución en [`NOTICE`](NOTICE).

Las integraciones son no oficiales. Las marcas pertenecen a sus titulares. Revisa las condiciones de cada tienda antes de habilitar automatizaciones autenticadas.
