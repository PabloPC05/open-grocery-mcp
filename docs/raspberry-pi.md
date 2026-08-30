# Despliegue en Raspberry Pi

Esta guía documenta el despliegue de Open Grocery MCP en Raspberry Pi 4 o 5 con recursos limitados (RAM/CPU), diseñado para ejecutarse 24/7 desde una IP residencial.

## Caso de uso

- **Catálogo y comparación**: Búsqueda de productos, comparación de cestas, ofertas
- **HTTP cookies autenticadas**: Lectura de `storage_state.json` como cookies httpx (sin proceso de navegador)
- **IP residencial**: Evita bloqueos WAF de Carrefour y Eroski que afectan a IPs de datacenter
- **Sin Playwright**: No se instala Chromium ni dependencias de navegador

## Requisitos

- Raspberry Pi 4 (2GB RAM mínimo) o Raspberry Pi 5
- Raspbian/Raspberry Pi OS (64-bit recomendado)
- Docker y Docker Compose instalados
- Conexión a internet estable

## Instalación rápida

### 1. Clonar el repositorio

```bash
git clone https://github.com/PabloPC05/open-grocery-mcp.git
cd open-grocery-mcp
```

### 2. Construir la imagen Docker

```bash
docker compose build
```

La construcción genera una imagen multi-arquitectura optimizada para `linux/arm64` (Raspberry Pi) y `linux/amd64`.

### 3. Iniciar el servicio

```bash
docker compose up -d
```

El servidor MCP escucha en `http://127.0.0.1:8000/mcp` (solo localhost por seguridad).

### 4. Verificar el estado

```bash
docker compose ps
curl http://127.0.0.1:8000/health
```

## Configuración de sesiones autenticadas

Si tienes sesiones autenticadas (`storage_state.json`) desde tu ordenador portátil:

```bash
# Copiar storage_state.json desde el portátil
scp laptop:~/.open-grocery-mcp/mercadona/storage_state.json \
    ~/.open-grocery-mcp/mercadona/

scp laptop:~/.open-grocery-mcp/gadis/storage_state.json \
    ~/.open-grocery-mcp/gadis/
```

El servidor leerá estos archivos como cookies httpx. **No se inicia ningún navegador**.

Para crear sesiones autenticadas localmente, usa el MCP local en tu portátil con Playwright:

```bash
# En el portátil (con Playwright instalado)
python -m open_grocery_mcp.tools.login_tool --store mercadona
```

Consulta [`authenticated-workflows.md`](./authenticated-workflows.md) para más detalles.

## Acceso remoto seguro con Tailscale

Para acceder al MCP desde otros dispositivos de forma segura:

1. **Instala Tailscale en la Raspberry Pi**:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

2. **Instala Tailscale en el cliente** (portátil, teléfono)

3. **Conecta al MCP usando la IP de Tailscale**:

```
http://100.x.y.z:8000/mcp
```

Reemplaza `100.x.y.z` con la IP de Tailscale de tu Raspberry Pi (visible en `tailscale ip`).

**⚠️ Seguridad**: NO expongas el puerto 8000 a internet público. Usa siempre:
- `127.0.0.1:8000` para acceso local
- Tailscale u otra VPN para acceso remoto
- Nunca `0.0.0.0:8000` sin firewall/autenticación

## Configuración de recursos

El `compose.yaml` limita los recursos para Raspberry Pi:

```yaml
deploy:
  resources:
    limits:
      cpus: '1.0'
      memory: 256M
    reservations:
      cpus: '0.5'
      memory: 128M
```

Ajusta estos límites según tu modelo de Raspberry Pi:

- **Pi 4 (2GB)**: Límites por defecto son adecuados
- **Pi 4 (4GB/8GB)** o **Pi 5**: Puedes aumentar a `memory: 512M`

## Estructura de directorios

```
~/.open-grocery-mcp/
├── mercadona/
│   └── storage_state.json    # Cookies de sesión Mercadona (httpx)
├── gadis/
│   └── storage_state.json    # Cookies de sesión Gadis (httpx)
├── froiz/
│   └── storage_state.json    # Cookies de sesión Froiz (httpx)
├── eroski/
│   └── storage_state.json    # Cookies de sesión Eroski (httpx)
├── shared_addresses.json     # Direcciones postales compartidas
├── shopping_lists.json       # Listas de compra recurrentes
└── shopping_profile.json     # Perfil de compra (presupuesto, alergias)
```

Estos archivos se montan como volumen Docker y persisten entre reinicios.

## Comandos útiles

```bash
# Ver logs en tiempo real
docker compose logs -f

# Reiniciar el servicio
docker compose restart

# Detener el servicio
docker compose down

# Reconstruir después de actualizar el código
git pull
docker compose build
docker compose up -d

# Verificar uso de recursos
docker stats open-grocery-mcp
```

## Limitaciones del despliegue Docker

### Sin Playwright

La imagen Docker **NO incluye Playwright ni Chromium**. Las siguientes herramientas MCP no están disponibles:

- `login_with_browser()` — Login interactivo con navegador
- Operaciones de carrito/checkout que requieren navegador como fallback

### Solo HTTP y cookies

El servidor Docker usa **solo clientes HTTP** con cookies leídas de `storage_state.json`:

- ✅ Catálogo público (todos los supermercados)
- ✅ Comparación de cestas
- ✅ Carrito HTTP autenticado (Mercadona, Gadis con cookies válidas)
- ✅ Ofertas y búsqueda expandida
- ❌ Login interactivo (requiere Playwright)
- ❌ Operaciones de navegador (fallback de Gadis/Froiz)

Para login o acciones de navegador, usa el **MCP local** en tu portátil con `[browser]` extra instalado.

## Actualización de código

```bash
cd open-grocery-mcp
git pull
docker compose build
docker compose up -d
```

Las sesiones, listas y direcciones se conservan en el volumen.

## Solución de problemas

### El contenedor no arranca

```bash
# Ver logs completos
docker compose logs

# Verificar recursos disponibles
free -h
docker system df
```

### Error de memoria

Reduce el límite de memoria en `compose.yaml`:

```yaml
limits:
  memory: 192M
```

O cierra otros servicios en la Raspberry Pi.

### Sesiones caducadas

Las cookies de `storage_state.json` expiran. Refresca la sesión desde tu portátil:

```bash
# En el portátil
python -m open_grocery_mcp.tools.login_tool --store mercadona

# Copiar a la Raspberry Pi
scp ~/.open-grocery-mcp/mercadona/storage_state.json \
    pi@raspberrypi:~/.open-grocery-mcp/mercadona/
```

### Puerto 8000 ocupado

Cambia el puerto en `compose.yaml`:

```yaml
ports:
  - "127.0.0.1:8080:8000"
```

Luego accede a `http://127.0.0.1:8080/mcp`.

## Comparación con Vercel

| Característica | Raspberry Pi | Vercel |
|---|---|---|
| **Coste** | Electricidad (~5€/año) | Gratis (hobby) |
| **IP** | Residencial (evita WAF) | Datacenter (bloqueado por Carrefour/Eroski) |
| **Uptime** | 24/7 local | 24/7 global |
| **Estado** | Persistente | Efímero (solo /tmp) |
| **Sesiones** | Cookies httpx locales | No disponibles (sin auth) |
| **Latencia** | Red local | Global CDN |
| **Playwright** | No (imagen ligera) | No (sin browser) |

Usa **Raspberry Pi** para:
- Catálogo autenticado de Carrefour/Eroski (IP residencial)
- Carrito HTTP con sesiones persistentes
- Acceso local/VPN 24/7

Usa **Vercel** para:
- Catálogo público de Mercadona/Gadis/Día
- Comparación de cestas sin autenticación
- Acceso público global

## Referencias

- [README principal](../README.md)
- [Authenticated workflows](./authenticated-workflows.md)
- [MCP tool reference](./mcp-tool-reference.md)
- [Vercel deployment](./vercel-deployment.md)
