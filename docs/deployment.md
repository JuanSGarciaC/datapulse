# Despliegue y operación

Dos formas de ejecutar DataPulse. **Docker Compose es la recomendada** (verificada de punta a
punta en CI en cada push). La ejecución nativa sirve para desarrollar.

| Modo | Verificado en | Qué necesitas |
|------|---------------|---------------|
| Docker Compose | CI (Ubuntu 24.04): build, migraciones, flujo diario, API, dashboard y pruebas E2E | Docker Desktop (Windows/macOS) o Docker Engine + Compose v2 |
| Nativo | Linux (pruebas locales y E2E con todos los servicios como procesos) | Python 3.13 vía `uv`, PostgreSQL 16 (puede ser el contenedor `postgres`) |

> **Limitación honesta:** `scripts/dev.ps1` está escrito para PowerShell 5.1+ y 7+, pero el
> entorno donde se desarrolló no tenía PowerShell, así que **no se ejecutó en Windows real**.
> Los comandos que invoca (`uv`, `docker compose`, `python -m datapulse...`) sí están
> verificados. Si encuentras un fallo en Windows, abre un issue con la salida.

## 1. Docker Compose (Windows, macOS, Linux)

```powershell
git clone https://github.com/JuanSGarciaC/datapulse.git
cd datapulse
Copy-Item .env.example .env          # Linux/macOS: cp .env.example .env
# Edita .env: cambia TODAS las contraseñas y claves "change-me-*"
docker compose up --build -d
docker compose ps                     # todos "healthy" (migrate termina con "exited (0)")
```

Cargar los datos de ejemplo y ejecutar el pipeline completo dentro de los contenedores:

```powershell
# 1) Deja los archivos en data/landing, como haría un proceso externo (desde tu máquina).
Copy-Item -Recurse -Force data\samples\customers, data\samples\orders data\landing\
#    Linux/macOS: cp -r data/samples/customers data/samples/orders data/landing/
# 2) Ejecuta el flujo diario en el contenedor del worker (productos: desde mock-source).
docker compose run --rm worker python -m datapulse.flows run daily
```

> La copia se hace desde el host a propósito: el contenedor corre como usuario sin
> privilegios (uid 10001) y, en Linux, no puede escribir en una carpeta montada que pertenece
> a tu usuario. Es el mismo procedimiento que usa el job de Docker en CI.

| Servicio | URL (solo `127.0.0.1`) | Para qué |
|----------|------------------------|----------|
| Dashboard | <http://localhost:8501> | Negocio, calidad de datos, pipelines |
| API (Swagger) | <http://localhost:8000/docs> | Indicadores y resultados; cabecera `X-API-Key` |
| Prefect UI | <http://localhost:4200> | Flujos, ejecuciones, reintentos, programación |
| API simulada | <http://localhost:8081/docs> | Fuente de productos con fallos inyectables |
| PostgreSQL | `localhost:5433` | Usuario administrador solo para migraciones |

El `worker` sirve dos despliegues programados: `daily` (cron `DATAPULSE_FLOW_DAILY_CRON`,
06:00 UTC por defecto) y `products-incremental` (cada `DATAPULSE_FLOW_PRODUCTS_INTERVAL_MINUTES`).

### Windows: problemas frecuentes

| Síntoma | Causa | Solución |
|---------|-------|----------|
| `port is already allocated` | Otro servicio usa 5433/8000/8501/4200/8081 | Cambia `*_HOST_PORT` en `.env` |
| `migrate` falla con `no such file` en un script | Finales de línea CRLF | El repo fuerza LF con `.gitattributes`; vuelve a clonar sin `core.autocrlf=true` |
| Contenedores lentos o reiniciándose | Poca memoria para WSL2 | Docker Desktop → Settings → Resources: 4 GB o más |
| `running scripts is disabled` | Política de ejecución | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| El worker no encuentra los productos | Usaste la URL de tu máquina dentro de Docker | Dentro de Compose la fuente es `DOCKER_PRODUCTS_API_URL` (`http://mock-source:8081`); `DATAPULSE_INGEST_PRODUCTS_API_URL` es para ejecución nativa |

## 2. Nativo (desarrollo)

```powershell
Copy-Item .env.example .env            # y cambia las contraseñas
.\scripts\dev.ps1 setup                # dependencias
docker compose up -d postgres          # o un PostgreSQL 16 propio (ajusta DATAPULSE_DB_*)
.\scripts\dev.ps1 migrate              # migraciones + usuarios de servicio
.\scripts\dev.ps1 mock                 # terminal 1
.\scripts\dev.ps1 pipeline             # terminal 2: ingesta + dbt
.\scripts\dev.ps1 api                  # terminal 2: API con el usuario de solo lectura
.\scripts\dev.ps1 dashboard            # terminal 3
```

Las mismas tareas existen en el `Makefile` para Linux/macOS (`make migrate`, `make pipeline`...).

## 3. Operación

### Disparar pipelines

| Cómo | Comando | Notas |
|------|---------|-------|
| API (admin) | `POST /v1/admin/pipelines/{daily\|products}/runs` con la clave de `DATAPULSE_API_ADMIN_KEY` | Encola en Prefect y responde 202 con `flow_run_id`. Sin clave admin configurada, el endpoint no existe (404) |
| Prefect UI | *Deployments → Run* | |
| CLI | `python -m datapulse.flows run {daily,customers,orders,products,transform}` | `--full-refresh` reconstruye los modelos incrementales de dbt |

`--full-refresh` **no** se permite desde la API a propósito: es una operación costosa que
debe decidir una persona con acceso al servidor.

### Reprocesar y recuperar

- **Reintentar es seguro.** Un archivo ya cargado se omite por su hash; un lote fallido se
  reintenta reutilizando su fila del registro. La marca de agua de la API solo avanza si la
  carga confirma (ver [pipeline.md](pipeline.md#idempotencia-y-recuperación)).
- **Reconstruir los marts desde `raw`:** `python -m datapulse.flows run transform --full-refresh`.
  `raw` nunca se modifica, así que el resultado es el mismo que el incremental (lo verifica
  `test_incremental_day_by_day_equals_full_refresh`).
- **Investigar un fallo:** pestaña *Pipelines* del dashboard → detalle de la ejecución
  (tareas, intentos, error), o `GET /v1/pipeline-runs/{run_id}`. El `orchestrator_run_id`
  enlaza con la ejecución en Prefect.

### Rotar secretos

| Secreto | Cómo rotarlo sin cortar el servicio |
|---------|-------------------------------------|
| Claves de la API | Añade la nueva a `DATAPULSE_API_KEYS` (lista separada por comas), reinicia `api`, migra los clientes y elimina la vieja |
| Clave del dashboard | Es una entrada más de `DATAPULSE_API_KEYS`: misma operación, y actualiza `DATAPULSE_DASHBOARD_API_KEY` |
| Contraseñas de usuarios de servicio | Cambia `DATAPULSE_*_DB_PASSWORD` y `docker compose run --rm migrate`: el bootstrap es idempotente y hace `ALTER ROLE ... PASSWORD` |
| Token de la API simulada | `DATAPULSE_MOCK_ADMIN_TOKEN` y reinicia `mock-source` |

### Copias de seguridad

```powershell
docker compose exec -T postgres pg_dump -U datapulse_admin -d datapulse -Fc > datapulse.dump
docker compose exec -T postgres pg_restore -U datapulse_admin -d datapulse --clean < datapulse.dump
```

`raw` y `ops` son la fuente de verdad: `staging` y `marts` se regeneran con dbt.

## 4. Antes de un entorno real

Este proyecto es un portafolio: el stack está pensado para una máquina. Para producción:

1. `DATAPULSE_ENV=prod` (exige contraseñas de 16 o más caracteres) y `DATAPULSE_API_DOCS_ENABLED=false`.
2. Secretos desde un gestor (Vault, AWS Secrets Manager, Docker secrets), no desde `.env`.
3. TLS delante de la API y el dashboard (proxy inverso); hoy solo escuchan en `127.0.0.1`.
4. PostgreSQL gestionado con copias automáticas y `sslmode=verify-full` (`DATAPULSE_DB_SSLMODE`).
5. Autenticación de usuarios en el dashboard (hoy es una herramienta interna sin login).
6. Sustituir la API simulada por la fuente real (`DOCKER_PRODUCTS_API_URL=https://dummyjson.com`
   ya funciona: el contrato se verifica cada semana en CI).
