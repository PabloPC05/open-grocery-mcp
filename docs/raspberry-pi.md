# Raspberry Pi Docker Deployment

Instrucciones para ejecutar Open Grocery MCP 24/7 en una Raspberry Pi 4/5 con recursos limitados.

## ¿Por qué Raspberry Pi?

El servidor MCP en una red residencial (IP doméstica) evita el bloqueo WAF de Cloudflare que afecta a Carrefour y Eroski desde IPs de datacenter (Vercel, AWS, GCP). La Raspberry Pi consume pocos recursos y puede ejecutarse continuamente sin afectar el rendimiento del sistema.

## Requisitos

- **Hardware**: Raspberry Pi 4 (2GB+ RAM) o Raspberry Pi 5
- **Sistema operativo**: Raspberry Pi OS de 64 bits (Debian-based)
- **Docker**: Docker Engine 20.10+ y Docker Compose v2+
- **Red**: Conexión estable a Internet, preferiblemente a través de Tailscale para acceso remoto seguro

## Instalación de Docker

Si Docker no está instalado en tu Raspberry Pi:

```bash
# Actualizar sistema
sudo apt update && sudo apt upgrade -y

# Instalar Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Añadir tu usuario al grupo docker
sudo usermod -aG docker $USER

# Aplicar cambios (o reiniciar sesión)
newgrp docker

# Verificar instalación
docker --version
docker compose version
```

## Despliegue

### 1. Clonar el repositorio

```bash
cd ~
git clone https://github.com/PabloPC05/open-grocery-mcp.git
cd open-grocery-mcp
git switch main  # O la rama deseada
```

### 2. Iniciar el servicio

```bash
docker compose up -d
```

El contenedor:
- Construye la imagen slim sin Playwright/Chromium
- Limita recursos a ~384MB RAM y 0.75 CPU
- Expone el puerto 8000 solo en localhost
- Persiste datos en un volumen Docker
- Se reinicia automáticamente si falla

### 3. Verificar el estado

```bash
# Ver logs
docker compose logs -f open-grocery-mcp

# Estado del contenedor
docker compose ps

# Health check
curl http://localhost:8000/health
```

Salida esperada de `/health`:

```json
{
  "name": "open-grocery-mcp",
  "version": "0.6.0",
  "mode": "catalogue_comparison_and_two_phase_retailer_actions",
  "retailer_writes_enabled": false,
  "order_submission_enabled": false,
  "stores": ["carrefour", "dia", "eroski", "froiz", "gadis", "mercadona"]
}
```

## Conexión desde Cursor/Grok Bot

### Opción A: Tailscale (recomendado)

1. Instala [Tailscale](https://tailscale.com/) en la Raspberry Pi:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

2. Conecta desde tu ordenador:

```bash
# Nombre Tailscale de tu Pi (ejemplo: raspberry-pi)
http://<nombre-tailscale-pi>:8000/mcp
```

3. Configura en Cursor:
   - Añade el MCP en configuración con URL: `http://<nombre-tailscale-pi>:8000/mcp`
   - El endpoint es accesible de forma segura a través de la red Tailscale

### Opción B: Acceso local (mismo WiFi)

Si estás en la misma red local:

```bash
# Encuentra la IP local de la Pi
hostname -I

# Conecta desde tu ordenador
http://192.168.x.x:8000/mcp
```

**⚠️ ADVERTENCIA**: No expongas el puerto 8000 directamente a Internet. El volumen contiene cookies de sesión y datos sensibles.

## Uso eficiente de recursos

### Imagen slim por defecto

La imagen Docker **NO incluye Playwright ni Chromium** para mantener el uso de RAM bajo:

- **Memoria idle objetivo**: ~150-200 MB RSS
- **Sin navegador**: Los catálogos funcionan con HTTP + cookies guardadas
- **Carrefour/Eroski**: Usa `storage_state.json` en lugar de lanzar Chromium

### Cómo funcionan Carrefour y Eroski sin navegador

1. **En tu ordenador** (con Chromium instalado):
   ```bash
   # Instalar con extras browser
   pip install -e ".[dev,browser]"
   playwright install chromium
   
   # Login interactivo
   open-grocery-mcp
   # Llamar a login_with_browser(store="carrefour") o login_eroski() desde el MCP
   ```

2. **Copiar el estado de sesión al volumen de Docker**:

   ```bash
   # Ubicación local del storage_state
   ~/.open-grocery-mcp/carrefour/storage_state.json
   ~/.open-grocery-mcp/eroski/storage_state.json
   
   # Copiar al volumen Docker
   docker cp ~/.open-grocery-mcp/carrefour/storage_state.json \
     open-grocery-mcp:/home/mcp/.open-grocery-mcp/carrefour/storage_state.json
   
   docker cp ~/.open-grocery-mcp/eroski/storage_state.json \
     open-grocery-mcp:/home/mcp/.open-grocery-mcp/eroski/storage_state.json
   ```

3. **El servidor MCP en la Pi reutiliza esas cookies**:
   - No se requiere reinicio
   - `search_products(store="carrefour", ...)` usa las cookies guardadas
   - `search_products(store="eroski", ...)` usa las cookies guardadas
   - Si expiran, simplemente vuelve a copiar un `storage_state.json` renovado

### Monitoreo de recursos

```bash
# Uso de memoria y CPU en tiempo real
docker stats open-grocery-mcp

# Si el contenedor consume más de ~400MB, investigar:
docker compose logs --tail=100 open-grocery-mcp
```

## Límites de recursos

Los límites en `docker-compose.yml` evitan que búsquedas pesadas saturen la Pi:

```yaml
mem_limit: 384m     # Máximo de RAM
cpus: 0.75          # Fracción de CPU
```

Si necesitas más recursos (por ejemplo, para búsquedas muy pesadas), edita `docker-compose.yml`:

```yaml
mem_limit: 512m
cpus: 1.0
```

Y reinicia:

```bash
docker compose down
docker compose up -d
```

## Persistencia de datos

El volumen `open-grocery-data` almacena:

- `carrefour/storage_state.json`, `eroski/storage_state.json`, etc.
- `shared_addresses.json` (direcciones postales compartidas)
- `shopping_lists.json` (listas de compra)
- `shopping_profile.json` (perfil de usuario)

Para hacer backup:

```bash
# Inspeccionar ubicación del volumen
docker volume inspect open-grocery-data

# Backup manual
docker run --rm -v open-grocery-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/open-grocery-backup.tar.gz -C /data .

# Restaurar
docker run --rm -v open-grocery-data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/open-grocery-backup.tar.gz -C /data
```

## Actualización

```bash
cd ~/open-grocery-mcp
git pull
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Troubleshooting

### El contenedor no arranca

```bash
# Ver logs completos
docker compose logs open-grocery-mcp

# Reconstruir imagen
docker compose build --no-cache
docker compose up -d
```

### Health check falla

```bash
# Verificar que el servidor responde
docker exec open-grocery-mcp curl -f http://localhost:8000/health

# Si no responde, verificar logs
docker compose logs --tail=50 open-grocery-mcp
```

### Memoria insuficiente

```bash
# Verificar uso de memoria
free -h
docker stats

# Aumentar límite en docker-compose.yml si la Pi tiene más RAM
mem_limit: 512m
```

### Carrefour/Eroski devuelven errores de autenticación

1. Verifica que `storage_state.json` existe en el volumen:
   ```bash
   docker exec open-grocery-mcp ls -lh /home/mcp/.open-grocery-mcp/carrefour/
   docker exec open-grocery-mcp ls -lh /home/mcp/.open-grocery-mcp/eroski/
   ```

2. Si faltan, cópialos desde tu ordenador (ver sección anterior)

3. Si las cookies expiraron, renueva el login en tu ordenador y vuelve a copiarlas

### Puerto 8000 ya en uso

```bash
# Verificar qué proceso usa el puerto
sudo lsof -i :8000

# Cambiar puerto en docker-compose.yml si es necesario
ports:
  - "127.0.0.1:8001:8000"  # Cambia 8001 por otro puerto libre
```

## Perfil browser opcional (NO recomendado en Pi)

Si absolutamente necesitas Playwright en la Pi (no recomendado por consumo de RAM):

1. Crea `Dockerfile.browser` con Playwright instalado
2. Usa un compose profile separado con más recursos:
   ```yaml
   mem_limit: 1g
   cpus: 1.5
   ```

**Mejor opción**: Mantén el login en un ordenador potente y solo copia `storage_state.json` a la Pi.

## Seguridad

- **No expongas el puerto 8000 a Internet**: Usa Tailscale o VPN
- **Las cookies de sesión son sensibles**: No las compartas ni las subas a Git
- **Writes deshabilitados por defecto**: Cambios en carritos reales están apagados
- **Orders deshabilitados por defecto**: No se puede realizar pedidos sin habilitar explícitamente

Para habilitar writes (solo si es necesario):

```yaml
environment:
  OPEN_GROCERY_ENABLE_RETAILER_WRITES: "1"
```

**Nunca habilites order submission en la Pi sin supervisión directa.**

## Comparación: Hosted vs Local

| Característica | Vercel (hosted) | Raspberry Pi (local) |
|----------------|-----------------|----------------------|
| IP residencial | ❌ Datacenter (bloqueado por WAF) | ✅ Red doméstica |
| Carrefour/Eroski | ❌ 403/503 desde Cloudflare | ✅ Funciona con cookies |
| Catálogos públicos | ✅ Mercadona, Gadis, Froiz, Día | ✅ Todos |
| Costo | ✅ Gratis (Vercel free tier) | ✅ ~5W consumo eléctrico |
| Disponibilidad | ✅ 24/7 global | ⚠️ Requiere Pi encendida |
| Latencia | ✅ CDN edge | ⚠️ Red local/Tailscale |
| Mantenimiento | ✅ Automático (push a main) | ⚠️ Manual (docker compose) |

**Recomendación**: Usa Vercel para catálogos públicos y la Pi solo para Carrefour/Eroski autenticados.

## Conclusión

El despliegue Docker en Raspberry Pi permite:
- Ejecutar Open Grocery MCP 24/7 con mínimos recursos
- Evitar bloqueos WAF de Carrefour y Eroski desde IP residencial
- Reutilizar cookies de sesión sin lanzar Chromium en cada búsqueda
- Mantener datos persistentes y seguros en red local

La imagen slim (~150-200MB idle) es suficiente para catálogo + comparación. Para login, usa un ordenador potente y copia `storage_state.json` al volumen Docker.
