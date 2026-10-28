# API de indicadores (fase 6)

Base: `http://localhost:8000`. Documentación interactiva (OpenAPI): `/docs`, que se desactiva
con `DATAPULSE_API_DOCS_ENABLED=false`.

## Autenticación

Todas las rutas `/v1` exigen la cabecera `X-API-Key`. La clave **no** se acepta en la URL, porque
acabaría en logs de proxies e historiales.

```powershell
# Primera clave de DATAPULSE_API_KEYS de tu .env (se lee del archivo; no la pegues en el código)
$key = (Select-String -Path .env -Pattern '^DATAPULSE_API_KEYS=').Line.Split('=', 2)[1].Split(',')[0]
$h = @{ 'X-API-Key' = $key }
Invoke-RestMethod -Uri 'http://localhost:8000/v1/kpis/summary?date_from=2026-01-01' -Headers $h
```
```bash
key=$(grep ^DATAPULSE_API_KEYS= .env | cut -d= -f2 | cut -d, -f1)
curl -H "X-API-Key: $key" "http://localhost:8000/v1/kpis/summary"
```

| Variable | Uso |
|----------|-----|
| `DATAPULSE_API_KEYS` | Claves de lectura separadas por comas (rotación sin corte). Mínimo 16 caracteres. |
| `DATAPULSE_API_ADMIN_KEY` | Solo para disparar pipelines. Si está vacía, `/v1/admin/*` responde 404. |

## Endpoints

| Método y ruta | Descripción | Parámetros |
|---------------|-------------|------------|
| `GET /health` | Vida del proceso (sin auth, sin BD) | — |
| `GET /health/ready` | Disponibilidad: comprueba la BD (503 si cae) | — |
| `GET /v1/kpis/summary` | Ingresos, pedidos, unidades, ticket medio, clientes activos, anulaciones | `date_from`, `date_to` |
| `GET /v1/sales/daily` | Ventas por día y categoría (paginado) | fechas, `category`, `limit`, `offset` |
| `GET /v1/sales/by-category` | Ventas agregadas por categoría | fechas |
| `GET /v1/products/top` | Productos más vendidos | `metric=revenue\|units`, `limit` ≤ 100, fechas |
| `GET /v1/pipeline-runs` | Ejecuciones (paginado) | `status`, `flow_name` |
| `GET /v1/pipeline-runs/{run_id}` | Detalle: tareas, intentos, duraciones, fallos de calidad | — |
| `GET /v1/ingestion/batches` | Lotes ingeridos con su sha256 (trazabilidad) | `source`, `status` |
| `GET /v1/metrics/volumes` | Volúmenes y duraciones por día y flujo | `days` ≤ 366 |
| `GET /v1/quality/summary` | Resumen por regla (filas evaluadas y fallidas, último resultado) | `since`, `layer` |
| `GET /v1/quality/results` | Resultados individuales (paginado) | `table_name`, `passed` |
| `POST /v1/admin/pipelines/{daily\|products}/runs` | Dispara un despliegue de Prefect (202) | clave admin |

**Paginación:** `limit` (por defecto 50, máximo 500) y `offset`. La respuesta trae `total`,
calculado con `count(*) OVER ()` en la misma consulta.

**Importes:** se devuelven como **texto decimal** (`"2613860.45"`), no como número de coma
flotante. Así el cliente no pierde precisión en dinero.

## Decisiones de seguridad

| Medida | Por qué |
|--------|---------|
| Conexión con `datapulse_api` (rol `dp_reader`) | Aunque hubiera un fallo en la API, la BD no permite escribir ni leer `raw` (correos en claro). Probado con el rol real. |
| Consultas SQL literales con parámetros enlazados | Ningún fragmento SQL se construye con texto; los filtros opcionales son `(:p IS NULL OR col = :p)`. |
| Validación antes de abrir conexión | La dependencia entrega el `Engine` y la conexión se abre dentro del endpoint: una petición inválida (422) no ocupa el pool. |
| Comparación de claves en tiempo constante | `hmac.compare_digest` sobre el hash de cada clave, recorriéndolas todas. |
| Falla cerrada | Sin claves configuradas, todo `/v1` responde 401. |
| Lista cerrada de despliegues | El endpoint admin solo acepta `daily` o `products` y siempre envía `trigger=api`; `full_refresh` está prohibido desde la API. |
| Errores genéricos | 503 si la BD o el orquestador no responden y 500 genérico, sin SQL, rutas ni trazas. |
| Cabeceras | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` en `/v1`; sin cabecera `Server`. |
| Límites | Tamaño de página, longitud de textos de filtro y rangos de `days`/`limit` acotados. |

**Fuera de alcance (documentado):** limitación de peticiones por cliente y TLS. En un despliegue
real los aporta un proxy inverso o un *API gateway* delante del servicio.
