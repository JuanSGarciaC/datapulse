# DataPulse

[![CI](https://github.com/JuanSGarciaC/datapulse/actions/workflows/ci.yml/badge.svg)](https://github.com/JuanSGarciaC/datapulse/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Plataforma de ingeniería de datos que convierte archivos (CSV, JSON) y APIs REST paginadas en
información **confiable, trazable e idempotente** para análisis de negocio.

> **Estado del proyecto: las 8 fases están implementadas y verificadas en CI**, incluida una
> prueba de extremo a extremo contra el stack en contenedores. Despliegue y operación en
> [docs/deployment.md](docs/deployment.md); limitaciones conocidas al final de este documento.

## Qué demuestra este proyecto

| Tema | Cómo | Dónde |
|------|------|-------|
| Separación por capas | Esquemas `raw` → `staging` → `marts` + `ops` | [architecture.md](docs/architecture.md) |
| Mínimo privilegio | Roles de grupo por componente; la API es solo lectura, `raw` es append-only | [ADR-0005](docs/adr/0005-least-privilege-roles.md) |
| Migraciones reversibles | Alembic, prueba de ida y vuelta y detección de deriva en CI | `migrations/`, `tests/integration/` |
| Idempotencia | Hash de contenido por lote, marcas de agua, claves únicas | [ADR-0004](docs/adr/0004-idempotency-strategy.md) |
| Gestión de secretos | Variables de entorno, `SecretStr`, redacción en logs, gitleaks en CI | `src/datapulse/config.py` |
| Calidad de código | ruff, mypy estricto (código y pruebas), bandit, pip-audit, cobertura ≥ 90 % | `.github/workflows/ci.yml` |
| Datos reproducibles | Generador determinista con 37 defectos documentados (respuesta conocida) | [sources.md](docs/sources.md) |
| Pruebas de contrato | Una suite contra la API simulada **y** contra DummyJSON real | [ADR-0006](docs/adr/0006-public-api-and-mock-contract.md) |
| Resiliencia | Reintentos con `Retry-After` y backoff con jitter; carga atómica por lote; bloqueo por fuente | [pipeline.md](docs/pipeline.md) |
| Calidad de datos | Contratos por fuente, cuarentena con motivo, presupuesto de errores, cambios de esquema | [ADR-0007](docs/adr/0007-validation-design.md) |
| API segura | Solo lectura con rol `dp_reader`, API key en tiempo constante, SQL sin interpolación, validación antes de conectar | [api.md](docs/api.md) |
| Modelado analítico | Estrella con SCD2 *point-in-time*, incremental probado igual a *full refresh*, conciliaciones | [data-model.md](docs/data-model.md), [ADR-0008](docs/adr/0008-scd2-and-incremental-models.md) |

## Stack

| Componente | Elección | Justificación |
|------------|----------|---------------|
| Lenguaje | Python 3.13 | Tipado moderno y soporte en todo el stack (verificado) |
| Base de datos | PostgreSQL 16 | Una instancia con esquemas por capa basta para este volumen |
| Migraciones | Alembic + SQLAlchemy 2.1 (Core) | Migraciones explícitas y revisables |
| Orquestación | Prefect 3.9 | La opción más simple que es orquestación real ([ADR-0001](docs/adr/0001-orchestration-prefect.md)) |
| DataFrames *(fase 3)* | Polars 2.1 | Tipado estricto, ideal para validar ([ADR-0002](docs/adr/0002-dataframes-polars.md)) |
| Transformación | dbt-core 1.12 + dbt-postgres 1.11 | SQL versionado, pruebas y linaje; ejecutado con `dbtRunner` |
| API y API simulada | FastAPI 0.150 | Limitado por Prefect 3.9 (ver ADR-0001) |
| Cliente HTTP | httpx2 2.16 | Sucesor de httpx; Starlette 1.10 depreca httpx (ADR-0006) |
| API pública | [DummyJSON](https://dummyjson.com) | Gratuita, e-commerce, paginación real (ADR-0006) |
| Dashboard | Streamlit 1.66 + Altair | Python de punta a punta; consume la API sin credenciales de BD ([dashboard.md](docs/dashboard.md)) |
| Gestor de paquetes | uv | Lockfile reproducible y rápido |

## Inicio rápido con Docker (Windows, macOS o Linux)

Requisitos: [Docker Desktop](https://www.docker.com/products/docker-desktop/) y Git.

```powershell
git clone https://github.com/JuanSGarciaC/datapulse.git
cd datapulse
Copy-Item .env.example .env      # Linux/macOS: cp .env.example .env
# Edita .env y cambia TODAS las contraseñas "change-me-*"
docker compose up --build
```

Al terminar, el servicio `migrate` habrá aplicado las migraciones y creado los usuarios de
servicio. Quedan disponibles **solo en tu máquina**:

| Servicio | Dirección |
|----------|-----------|
| PostgreSQL | `localhost:5433` |
| Prefect (UI) | <http://localhost:4200> |
| API de indicadores (Swagger) | <http://localhost:8000/docs> |
| API simulada (Swagger) | <http://localhost:8081/docs> |
| **Dashboard** | <http://localhost:8501> |

Para provocar fallos en la API simulada (necesita el token de `.env`):

```powershell
$h = @{ 'X-Mock-Admin-Token' = 'change-me-mock-admin' }
Invoke-RestMethod -Method Put -Uri http://localhost:8081/_mock/faults -Headers $h `
  -ContentType 'application/json' -Body '{"fail_offsets":{"30":2},"rate_limit":60}'
```

```powershell
docker compose down        # detiene (conserva datos)
docker compose down -v     # detiene y BORRA el volumen de datos
```

## Desarrollo local en Windows (PowerShell)

Requisitos: Git, Docker Desktop y [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`winget install astral-sh.uv`). uv instala Python 3.13 automáticamente si no lo tienes.

```powershell
Copy-Item .env.example .env              # y cambia las contraseñas
.\scripts\dev.ps1 setup                  # dependencias (incluye desarrollo)
docker compose up -d postgres            # solo la base de datos
.\scripts\dev.ps1 migrate                # migraciones + usuarios de servicio
.\scripts\dev.ps1 createtestdb           # una vez: base para pruebas de integración
.\scripts\dev.ps1 check                  # lint + tipos + pruebas + seguridad
.\scripts\dev.ps1 generate               # regenera data/samples (mismos bytes siempre)
.\scripts\dev.ps1 mock                   # API simulada en http://127.0.0.1:8081
.\scripts\dev.ps1 test-live              # contrato contra DummyJSON real (internet)
.\scripts\dev.ps1 ingest-samples         # carga los datos de ejemplo (necesita postgres + mock)
.\scripts\dev.ps1 dbt-build              # modelos + pruebas de dbt (usuario datapulse_dbt)
```

Todo en nativo, sin Docker salvo PostgreSQL (una terminal por servicio):

```powershell
.\scripts\dev.ps1 mock        # terminal 1: fuente de productos en :8081
.\scripts\dev.ps1 pipeline    # terminal 2: ingesta + dbt (flujo diario de Prefect)
.\scripts\dev.ps1 api         # terminal 2: API en :8000 con el usuario de solo lectura
.\scripts\dev.ps1 dashboard   # terminal 3: dashboard en :8501
```

> Si PowerShell bloquea el script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

En Linux/macOS los mismos comandos existen en el `Makefile` (`make setup`, `make check`, …).

## Pruebas

| Tipo | Qué cubre | Requiere |
|------|-----------|----------|
| Unitarias (`tests/unit`) | Configuración, escapado de URL, redacción de secretos, logging JSON, validación de contraseñas | Nada |
| Unitarias (`tests/unit`) — fase 2 | Generador (reproducibilidad, independencia de flujos, cada defecto presente en los archivos), API simulada (fallos, rate limit, reloj, admin) | Nada |
| Integración (`tests/integration`) | Migraciones contra PostgreSQL real: estructura, ida y vuelta, constraints, deriva, matriz de permisos, bootstrap de punta a punta | PostgreSQL |
| Integración — fase 3 | Respuesta conocida contra `manifest.json`, idempotencia al re-ingerir, incremental por marca de agua, atomicidad ante fallos, bloqueo concurrente, permisos de `dp_pipeline` | PostgreSQL |
| Integración — fase 4 | Prefect real (servidor efímero): flujo diario, reintento de tarea tras caída de la fuente, aislamiento de fallos y recuperación | PostgreSQL |
| Integración — fase 5 | dbt real: granularidad, SCD2 *point-in-time*, versiones tardías, avisos de huérfanos exactos, incremental = *full refresh*, permisos de lectura | PostgreSQL |
| API — fase 6 | Unitarias (auth, validación, errores, cabeceras) e integración contra almacén real con rol `dp_reader`: cada KPI se compara con un cálculo SQL independiente | PostgreSQL |
| Contrato (`tests/contract`) | La misma suite contra la API simulada y contra DummyJSON real (`-m live`) | Internet para `live` |
| E2E (`tests/e2e`) | Caja negra contra el stack en contenedores: disparo por la API admin → Prefect → worker → fuente con fallos y límite de peticiones → API; defectos conocidos visibles por HTTP | Stack levantado (`-m e2e`, en CI) |

```powershell
.\scripts\dev.ps1 test-unit    # sin base de datos
.\scripts\dev.ps1 test         # todas (lee DATAPULSE_TEST_DB_* de .env)
```

Las pruebas de integración se **omiten** localmente si no hay base de datos, pero en CI
(`CI=true`) **fallan**: un verde en CI siempre significa que se ejecutaron.

## Estructura

```
src/datapulse/
  config.py              configuración tipada desde variables de entorno
  db/schema.py           tablas gestionadas por Alembic (fuente de verdad en código)
  db/engine.py           engine y comprobación de conexión
  db/bootstrap.py        usuarios de servicio con mínimo privilegio
  observability/         logging JSON estructurado con redacción de secretos
  synthetic/             generador determinista de datos con defectos documentados
  mock_source/           API simulada (contrato DummyJSON + fallos inyectables)
  sources/contracts.py   contrato de la API de productos (Pydantic)
  validation/            contratos por fuente, motor tabular (Polars), validador de pedidos
  ingestion/             cliente REST con reintentos, carga idempotente, casos de uso y CLI
  flows/                 flujos y despliegues de Prefect (reintentos de tarea, programación)
  transform/             dbt desde Python (dbtRunner) y registro de sus pruebas en ops
  api/                   API de indicadores (FastAPI, solo lectura)
dbt/                     modelos staging y marts, pruebas genéricas y singulares
data/samples/            datos de ejemplo versionados + manifest.json
migrations/              migraciones Alembic (raw, ops, roles y permisos)
tests/{unit,integration}
docs/                    arquitectura y ADRs
scripts/dev.ps1          comandos para Windows
```

## Seguridad

- Ningún secreto se versiona: `.env` está en `.gitignore`; `.env.example` solo trae valores ficticios.
- Las contraseñas son `SecretStr`: no aparecen en `repr`, logs ni mensajes de error.
- Cada componente se conecta con su propio usuario y los permisos mínimos.
- PostgreSQL y la API simulada se publican solo en `127.0.0.1`. Los contenedores corren como usuario no root.
- Los endpoints de administración de la API simulada no existen sin token (404) y el token se compara en tiempo constante.
- Todos los datos son sintéticos; los correos usan `example.com` (dominio reservado, RFC 2606).
- CI ejecuta bandit, pip-audit y gitleaks sobre todo el historial.

## Roadmap

| Fase | Entregable | Estado |
|------|-----------|--------|
| 1 | Fundaciones: configuración, migraciones, roles, CI, Docker | ✅ Implementada |
| 2 | Datos sintéticos reproducibles + API simulada con fallos inyectables | ✅ Implementada |
| 3 | Ingesta CSV/JSON/REST, contratos de validación, cuarentena, idempotencia | ✅ Implementada |
| 4 | Orquestación con Prefect: reintentos, registro de ejecuciones, recuperación | ✅ Implementada |
| 5 | dbt: staging, SCD2, modelo estrella, pruebas y conciliación | ✅ Implementada |
| 6 | API de indicadores y resultados de pipelines | ✅ Implementada |
| 7 | Dashboard de calidad, volúmenes, tiempos y errores | ✅ Implementada |
| 8 | Integración final, pruebas E2E y documentación de despliegue | ✅ Implementada |

## Limitaciones conocidas

Lo que este proyecto **no** hace o no se pudo verificar, dicho explícitamente:

- **`scripts/dev.ps1` no se ejecutó en un Windows real** (el entorno de desarrollo no tenía
  PowerShell). Los comandos que llama sí están verificados; Docker Compose es la vía probada.
- **Carga incremental con escaneo completo:** DummyJSON no filtra por fecha, así que cada
  ejecución lee todo el catálogo y filtra en el cliente (`updatedAt` > marca de agua −
  solapamiento). Con 200 productos es trivial; con millones haría falta una fuente que
  filtre en el servidor o CDC.
- **Pensado para una máquina:** sin TLS, sin login en el dashboard y con secretos en `.env`.
  La lista de cambios para un entorno real está en [deployment.md](docs/deployment.md#4-antes-de-un-entorno-real).
- **`rows_evaluated` de las pruebas de dbt es desconocido:** dbt informa las filas que fallan,
  no las evaluadas; se registra así y se marca en `details` (ver [data-model.md](docs/data-model.md)).
- **dbt-core copia el `.env` a las variables del proceso** al importarse; DataPulse lo confina
  (ver [pipeline.md](docs/pipeline.md#hallazgo-dbt-core-copia-el-env-a-las-variables-del-proceso)),
  pero es un comportamiento de la dependencia, no del proyecto.

## Licencia

[MIT](LICENSE)
