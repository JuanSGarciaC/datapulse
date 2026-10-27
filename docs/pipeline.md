# Pipeline de ingesta (fase 3)

```mermaid
flowchart LR
    F[Archivo CSV/JSON<br/>o API paginada] --> H{¿sha256 del lote<br/>ya procesado?}
    H -- sí --> SKIP[skipped]
    H -- no --> V[Validación<br/>contrato + reglas]
    V -- error de esquema o<br/>tasa de error > 20 % --> REJ[rejected<br/>solo resultados de calidad]
    V -- filas válidas --> RAW[(raw.*<br/>ON CONFLICT _record_hash<br/>DO NOTHING)]
    V -- filas inválidas --> Q[(raw.quarantine)]
    V -- resultados por regla --> QR[(ops.quality_results)]
    RAW & Q & QR --> TX[[una transacción:<br/>+ registro del lote<br/>+ marca de agua]]
```

## Idempotencia y recuperación

| Situación | Qué pasa | Dónde se garantiza |
|-----------|----------|--------------------|
| Mismo archivo otra vez (aunque cambie el nombre) | `skipped` por sha256 del contenido | `ops.ingestion_registry` único `(source_name, content_sha256)` |
| Registro ya cargado en otro lote | Se ignora | `raw.*._record_hash` único + `ON CONFLICT DO NOTHING` |
| Nueva versión de un registro (`updated_at` posterior) | Se carga como fila nueva; staging elige la última | Hash distinto |
| Falla a mitad de la carga | No queda **nada** escrito; lote `failed`; el reintento reutiliza el `batch_id` | Una transacción por lote |
| La carga falla | La marca de agua **no** avanza | Se actualiza en la misma transacción |
| Dos ejecuciones simultáneas de la misma fuente | La segunda falla con `ConcurrentIngestionError` | `pg_try_advisory_xact_lock` |
| API: fallo transitorio (red, 429, 5xx) | Reintento con `Retry-After` o backoff exponencial con *jitter* | `ingestion/rest.py` |
| API: 4xx o contrato roto | Falla inmediata, sin reintentos inútiles | `ingestion/rest.py` |

## Carga incremental de la API

DummyJSON no filtra por fecha, así que se lee el catálogo y se filtra en el cliente:
`updatedAt > marca_de_agua − solapamiento` (60 min por defecto). El solapamiento recoge
cambios tardíos; lo ya cargado se descarta por hash. El lote se identifica por el hash del
conjunto de registros seleccionados: si no cambió nada, el lote se omite.

## Reglas de calidad (catálogo)

| Fuente | Reglas |
|--------|--------|
| Clientes | `not_null:{customer_id,email,updated_at}`, `format:{customer_id,email,country}`, `type:{signup_date,updated_at}`, `accepted_values:segment` |
| Pedidos | `not_null:{order_id,customer_id,order_ts,updated_at,status}`, `format:{order_id,customer_id,currency}`, `type:{order_ts,updated_at,items,items.*}`, `not_empty:items`, `range:items.{quantity,product_id,unit_price}`, `accepted_values:status` |
| Productos | `not_null:{id,title,category,price,updatedAt}`, `type:*` numéricos y fechas, `range:{price,discountPercentage,rating,stock,id}` |
| Todas | `duplicate_in_batch` (aviso), `schema_change:*` (aviso o rechazo), `error_budget` |

## Ejecutar

```powershell
# Con PostgreSQL y la API simulada levantados (docker compose up -d postgres migrate mock-source)
uv run python -m datapulse.ingestion customers data/samples/customers/*.csv
uv run python -m datapulse.ingestion orders data/samples/orders/*.json
uv run python -m datapulse.ingestion products            # o --base-url https://dummyjson.com
```

Cada comando imprime un resumen JSON por lote y deja la traza en `ops.pipeline_runs`,
`ops.task_runs`, `ops.ingestion_registry` y `ops.quality_results`.
