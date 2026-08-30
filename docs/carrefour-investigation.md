# Investigación: Carrefour España - Catálogo Público

**Fecha**: 30 de agosto de 2026  
**Estado**: No hay catálogo público accesible sin resolver CAPTCHA/WAF  
**Conclusión**: No se puede implementar un proveedor público de Carrefour en este momento

## Resumen Ejecutivo

Carrefour España (www.carrefour.es) no proporciona una API pública documentada para acceder a su catálogo de productos. El sitio web está protegido por Cloudflare con políticas anti-bot agresivas que bloquean todo acceso automatizado mediante HTTP 403/503, requiriendo resolución de CAPTCHAs o desafíos JavaScript para acceso humano.

## Métodos de Investigación

### 1. Análisis de Tráfico de Red (Playwright)

**Herramienta**: `tools/investigate_carrefour.py`, `tools/deep_carrefour_investigation.py`

**Descubrimientos**:
- Se identificó un endpoint interno de búsqueda: `/search-api/suggestions/v1/empathize` (plataforma Empathy)
- Este endpoint requiere cookies y tokens de sesión válidos generados por Cloudflare
- El acceso directo mediante HTTP retorna 403 Forbidden

**Ejemplo de URL capturada**:
```
https://www.carrefour.es/search-api/suggestions/v1/empathize
  ?instance=x-carrefour
  &catalog=food
  &lang=es
  &citrusCatalog=food
```

### 2. Acceso HTTP Directo

**Herramienta**: `tools/test_carrefour_api.py`

**Resultados**:
```
GET https://www.carrefour.es/search-api/search/v1/search → 403
GET https://www.carrefour.es/api/search → 403
GET https://www.carrefour.es/api/products/search → 403
GET https://www.carrefour.es/supermercado/api/search → 403
```

Todos los endpoints intentados devuelven HTTP 403 con headers de Cloudflare:
```
server: cloudflare
cf-ray: a333b5c269ecf2c6-IAD
```

### 3. Análisis HTML Renderizado

**Herramienta**: `tools/check_carrefour_html.py`

**Resultado**: HTTP 503 Service Unavailable

El contenido devuelto por Cloudflare es simplemente:
```html
<html><head><meta name="color-scheme" content="light dark"></head>
<body><pre>Service Unavailable</pre></body></html>
```

### 4. Búsqueda de Documentación Oficial

**Fuentes consultadas**:
- Sitio web oficial de Carrefour España
- robots.txt (confirmó bloqueo de `/search-api/ajax/*` y otros endpoints)
- Búsqueda web de APIs públicas de Carrefour

**Hallazgos**:
1. **No existe API pública para consumidores** ([Parse.bot](https://parse.bot/marketplace/975df0bf-702c-4272-936a-9012ee2faf0d/carrefour-es-api), [Pepesto](https://www.pepesto.com/supermarkets/carrefour-es/))
2. **API de Mirakl para vendedores**: Solo accesible con Shop ID y API Key del marketplace (https://carrefoures-prod.mirakl.net/api)
3. **Servicios de terceros**: Parse.bot y Pepesto ofrecen scraping comercial, no son APIs oficiales

## Protecciones Anti-Bot Detectadas

### Cloudflare WAF
- HTTP 403 Forbidden en peticiones sin cookies de sesión válidas
- HTTP 503 Service Unavailable para URLs directas de búsqueda
- Requiere resolución de desafíos JavaScript
- Cookies de sesión `__cf_bm` con validación por dominio y caducidad

### Características de la Protección
```
set-cookie: __cf_bm=...; HttpOnly; SameSite=None; Secure; 
           Path=/; Domain=www.carrefour.es; 
           Expires=Sun, 30 Aug 2026 12:43:31 GMT
strict-transport-security: max-age=31536000
x-content-type-options: nosniff
```

## Opciones No Viables (Por Diseño del Proyecto)

Según `AGENTS.md` y las instrucciones del proyecto:

1. ❌ **Resolver CAPTCHAs**: Prohibido explícitamente
2. ❌ **Bypass de WAF**: Fuera de alcance, requeriría técnicas anti-detección
3. ❌ **Scraping con cookies manuales**: Solo local, no funciona en Vercel/Lambda hosted
4. ❌ **APIs de terceros**: No son contratos públicos oficiales de Carrefour
5. ❌ **Crear proveedor falso**: Prohibido por las instrucciones

## Comparación con Otros Proveedores

### Mercadona
✅ API pública Algolia con credenciales estables  
✅ Endpoints JSON documentables  
✅ Sin protección anti-bot agresiva

### Eroski
✅ Búsqueda HTML parseable sin login  
✅ Estructura HTML estable  
✅ Validación de código postal sin bloqueo

### Carrefour
❌ Sin API pública documentada  
❌ Cloudflare WAF bloquea acceso automatizado  
❌ Requiere sesión de navegador con desafíos resueltos

## Recomendaciones

### A Corto Plazo
No implementar un proveedor de Carrefour España en `open-grocery-mcp` en este momento. Documentar el bloqueo en el README y este archivo.

### A Medio Plazo
1. **Monitorear cambios**: Revisar periódicamente si Carrefour publica una API pública
2. **Solicitud oficial**: Contactar con Carrefour para solicitar acceso API para desarrolladores
3. **Implementación local-only**: Si un usuario local puede mantener una sesión autenticada con `storage_state.json`, considerar un proveedor que:
   - Solo funcione localmente (no en hosted/Vercel)
   - Use cookies de navegador existentes
   - Detecte y falle limpiamente cuando no hay sesión válida
   - Documente claramente que no es público

### Contrato Esperado (Si Fuera Accesible)

Si Carrefour expusiera su API de búsqueda, el contrato sería:

**Endpoint de Búsqueda**:
```
GET /search-api/search/v1/search
  ?q=<query>
  &catalog=food
  &lang=es
  &limit=<limit>
  &offset=<offset>
```

**Headers Requeridos**:
```
Cookie: __cf_bm=<token>; [otras cookies de sesión]
User-Agent: <navegador estándar>
Accept: application/json
Referer: https://www.carrefour.es/supermercado
```

**Estructura de Respuesta Esperada** (basado en patrones de Empathy):
```json
{
  "products": [
    {
      "id": "string",
      "name": "string",
      "price": {
        "current": number,
        "currency": "EUR",
        "unit_price": number
      },
      "image_url": "string",
      "url": "string",
      "available": boolean
    }
  ],
  "total": number,
  "pagination": {...}
}
```

## Archivos Generados Durante Investigación

- `/workspace/tools/investigate_carrefour.py`
- `/workspace/tools/test_carrefour_api.py`
- `/workspace/tools/deep_carrefour_investigation.py`
- `/workspace/tools/check_carrefour_html.py`
- `/workspace/local-captures/carrefour_investigation.json`
- `/workspace/local-captures/carrefour_deep_investigation.json`
- `/workspace/local-captures/carrefour_search_page.html`

## Evidencia de Bloqueo

### Petición HTTP Directa
```bash
$ curl -I "https://www.carrefour.es/supermercado"
HTTP/2 403
server: cloudflare
```

### Petición con User-Agent
```bash
$ curl -H "User-Agent: Mozilla/5.0..." "https://www.carrefour.es/search-api/search/v1/search?q=leche"
HTTP/2 403
server: cloudflare
cf-ray: a333b5c269ecf2c6-IAD
```

### Playwright con Navegador Real
```
Response status: 503
HTML: <html><body><pre>Service Unavailable</pre></body></html>
```

## Conclusión

Carrefour España no puede añadirse como proveedor público de catálogo en `open-grocery-mcp` en su estado actual. La implementación requeriría:

1. Resolución de CAPTCHAs (prohibido)
2. Bypass de Cloudflare WAF (fuera de alcance)
3. Acceso a API oficial (no existe)

**Acción recomendada**: Documentar este hallazgo y esperar a que Carrefour publique una API pública oficial o modifique sus políticas anti-bot para permitir acceso programático legítimo.

---

**Investigación realizada por**: Cloud Agent  
**Commit**: [pendiente]  
**Branch**: `cursor/add-carrefour-spain-catalogue-1df7`
