"""API de indicadores y resultados de pipelines (solo lectura, salvo el disparo admin).

Sin ``from __future__ import annotations`` a propósito: FastAPI debe evaluar las
anotaciones que usan dependencias definidas dentro de las fábricas de routers.

Ejecutar: ``python -m datapulse.api`` (uvicorn con la fábrica ``create_app``).
En producción se conecta con ``datapulse_api`` (rol ``dp_reader``): aunque hubiera
un fallo en la API, la base de datos no permitiría escribir ni leer ``raw``.
"""

import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from datapulse.api import repository as repo
from datapulse.api.schemas import (
    Batch,
    CategorySales,
    DailySales,
    DailyVolume,
    Health,
    KpiSummary,
    OrderLineage,
    Page,
    PipelineRun,
    PipelineRunDetail,
    QualityResult,
    QualitySummary,
    QuarantinedRecord,
    QuarantinePreviewRow,
    RowIssue,
    RuleOutcome,
    SourceFreshness,
    TopProduct,
    TriggerRequest,
    TriggerResponse,
    ValidationPreview,
)
from datapulse.api.security import KeyChecker, require_key
from datapulse.api.settings import ApiSettings
from datapulse.config import get_settings
from datapulse.db.engine import build_engine
from datapulse.validation.files import validate_customers_csv, validate_orders_json
from datapulse.validation.report import ValidationReport

logger = logging.getLogger(__name__)

TriggerFn = Callable[[str, dict[str, Any]], tuple[uuid.UUID, str]]
# Lista cerrada de despliegues que se pueden disparar (nombre público → flujo/despliegue).
DEPLOYMENTS: dict[str, str] = {
    "daily": "daily-pipeline/daily",
    "products": "ingest-products/products-incremental",
}
RunStatus = Literal["running", "success", "failed", "partial"]
BatchStatus = Literal["processing", "success", "failed", "rejected"]
Layer = Literal["pre_load", "post_transform"]
# Validador en vivo: tope de tamaño y de filas listadas para que un archivo no agote memoria.
MAX_UPLOAD_BYTES: Final = 1_000_000
MAX_LISTED_ROWS: Final = 200
VALIDATORS: Final = {"customers": validate_customers_csv, "orders": validate_orders_json}
_EXAMPLE_CSV: Final = (
    "customer_id,first_name,last_name,email,city,country,segment,signup_date,updated_at\n"
    "C00009,Natalia,Peña,natalia.pena.9.at.example.com,Guadalajara,MX,enterprise,"
    "2024-07-22,2026-01-01T09:18:00Z\n"
)


def prefect_trigger(deployment: str, parameters: dict[str, Any]) -> tuple[uuid.UUID, str]:
    from prefect.client.schemas.objects import FlowRun  # noqa: PLC0415 - solo si se usa
    from prefect.deployments import run_deployment  # noqa: PLC0415

    # timeout=0: crea la ejecución y vuelve sin esperar a que termine.
    flow_run = cast(FlowRun, run_deployment(name=deployment, parameters=parameters, timeout=0))
    state = flow_run.state.type.value if flow_run.state else "SCHEDULED"
    return flow_run.id, state


# ------------------------------------------------------------------ dependencias
def get_engine(request: Request) -> Engine:
    # Se entrega el Engine, no una conexión: la conexión se abre dentro del endpoint,
    # DESPUÉS de validar la petición. Una petición inválida nunca ocupa el pool.
    engine: Engine = request.app.state.engine
    return engine


Db = Annotated[Engine, Depends(get_engine)]
DateFrom = Annotated[
    date | None, Query(description="Desde (incluido), AAAA-MM-DD", examples=["2026-01-01"])
]
DateTo = Annotated[
    date | None, Query(description="Hasta (incluido), AAAA-MM-DD", examples=["2026-01-03"])
]
ShortText = Annotated[str | None, Query(max_length=200)]


def _check_range(date_from: date | None, date_to: date | None) -> None:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from no puede ser posterior a date_to")


def _paging_dependency(settings: ApiSettings) -> Callable[..., tuple[int, int]]:
    def paging(
        limit: Annotated[int, Query(ge=1, le=settings.max_page_size)] = settings.default_page_size,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> tuple[int, int]:
        return limit, offset

    return paging


def _page(items: list[dict[str, Any]], total: int, page: tuple[int, int]) -> dict[str, Any]:
    return {"items": items, "limit": page[0], "offset": page[1], "total": total}


# ------------------------------------------------------------------ routers
def business_router() -> APIRouter:
    router = APIRouter(tags=["indicadores"])

    @router.get(
        "/kpis/summary",
        summary="Resumen de ventas: ingresos, pedidos, ticket medio",
        response_model=KpiSummary,
    )
    def kpis(db: Db, date_from: DateFrom = None, date_to: DateTo = None) -> dict[str, Any]:
        _check_range(date_from, date_to)
        with db.connect() as conn:
            return repo.kpi_summary(conn, date_from, date_to)

    @router.get(
        "/sales/by-category", summary="Ventas por categoría", response_model=list[CategorySales]
    )
    def by_category(
        db: Db, date_from: DateFrom = None, date_to: DateTo = None
    ) -> list[dict[str, Any]]:
        _check_range(date_from, date_to)
        with db.connect() as conn:
            return repo.sales_by_category(conn, date_from, date_to)

    @router.get("/products/top", summary="Productos más vendidos", response_model=list[TopProduct])
    def top(
        db: Db,
        metric: Literal["revenue", "units"] = "revenue",
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> list[dict[str, Any]]:
        _check_range(date_from, date_to)
        with db.connect() as conn:
            return repo.top_products(
                conn, metric=metric, limit=limit, date_from=date_from, date_to=date_to
            )

    return router


def paged_router(paging: Callable[..., tuple[int, int]]) -> APIRouter:
    router = APIRouter()
    Paging = Annotated[tuple[int, int], Depends(paging)]  # noqa: N806

    @router.get(
        "/sales/daily",
        summary="Ventas por día y categoría",
        response_model=Page[DailySales],
        tags=["indicadores"],
    )
    def daily(
        db: Db,
        page: Paging,
        date_from: DateFrom = None,
        date_to: DateTo = None,
        category: ShortText = None,
    ) -> dict[str, Any]:
        _check_range(date_from, date_to)
        with db.connect() as conn:
            items, total = repo.daily_sales(
                conn, date_from=date_from, date_to=date_to, category=category,
                limit=page[0], offset=page[1],
            )  # fmt: skip
        return _page(items, total, page)

    @router.get(
        "/pipeline-runs",
        summary="Ejecuciones de los pipelines",
        response_model=Page[PipelineRun],
        tags=["pipelines"],
    )
    def runs(
        db: Db, page: Paging, status: RunStatus | None = None, flow_name: ShortText = None
    ) -> dict[str, Any]:
        with db.connect() as conn:
            items, total = repo.pipeline_runs(
                conn, status=status, flow_name=flow_name, limit=page[0], offset=page[1]
            )
        return _page(items, total, page)

    @router.get(
        "/ingestion/batches",
        summary="Lotes ingeridos con su sha256",
        response_model=Page[Batch],
        tags=["pipelines"],
    )
    def batches(
        db: Db, page: Paging, source: ShortText = None, status: BatchStatus | None = None
    ) -> dict[str, Any]:
        with db.connect() as conn:
            items, total = repo.batches(
                conn, source=source, status=status, limit=page[0], offset=page[1]
            )
        return _page(items, total, page)

    @router.get(
        "/quality/results",
        summary="Resultados de cada regla de calidad",
        response_model=Page[QualityResult],
        tags=["calidad"],
    )
    def quality_results(
        db: Db, page: Paging, table_name: ShortText = None, passed: bool | None = None
    ) -> dict[str, Any]:
        with db.connect() as conn:
            items, total = repo.quality_results(
                conn, table_name=table_name, passed=passed, limit=page[0], offset=page[1]
            )
        return _page(items, total, page)

    @router.get(
        "/quality/quarantine",
        summary="Filas en cuarentena, con motivo y archivo",
        response_model=Page[QuarantinedRecord],
        tags=["calidad"],
    )
    def quarantine(
        db: Db,
        page: Paging,
        *,
        source: ShortText = None,
        rule: ShortText = None,
        field: ShortText = None,
        record_key: ShortText = None,
    ) -> dict[str, Any]:
        """Filas apartadas por los contratos: motivo, lote y archivo. Nombres fuera, email
        enmascarado (la vista ``ops.v_quarantine`` lo hace antes de que llegue a la API)."""
        with db.connect() as conn:
            items, total = repo.quarantine(
                conn, source=source, rule=rule, field=field, record_key=record_key,
                limit=page[0], offset=page[1],
            )  # fmt: skip
        return _page(items, total, page)

    return router


def _freshness(row: dict[str, Any], sla_hours: float) -> dict[str, Any]:
    last = row["last_success_at"]
    age = None if last is None else (row["checked_at"] - last).total_seconds() / 3600
    status = "never" if age is None else ("fresh" if age <= sla_hours else "stale")
    row = {k: v for k, v in row.items() if k != "checked_at"}
    return row | {"age_hours": None if age is None else round(age, 1), "sla_hours": sla_hours,
                  "status": status}  # fmt: skip


def ops_router(settings: ApiSettings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/quality/freshness",
        summary="Frescura de cada fuente frente al SLA",
        response_model=list[SourceFreshness],
        tags=["calidad"],
    )
    def freshness(db: Db) -> list[dict[str, Any]]:
        """Por fuente: última carga buena, su antigüedad y si cumple el SLA de frescura."""
        with db.connect() as conn:
            rows = repo.freshness(conn)
        return [_freshness(r, settings.freshness_sla_hours) for r in rows]

    @router.get(
        "/pipeline-runs/{run_id}",
        summary="Detalle de una ejecución: tareas e intentos",
        response_model=PipelineRunDetail,
        tags=["pipelines"],
    )
    def run_detail(db: Db, run_id: uuid.UUID) -> dict[str, Any]:
        with db.connect() as conn:
            run = repo.pipeline_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="ejecución no encontrada")
        return run

    @router.get(
        "/lineage/orders/{order_id}",
        summary="Linaje de un pedido, del indicador al archivo",
        response_model=OrderLineage,
        tags=["linaje"],
    )
    def order_lineage(
        db: Db,
        order_id: Annotated[str, Path(pattern=r"^O\d{6}$", examples=["O000065"])],
    ) -> dict[str, Any]:
        """Cada línea del hecho, cada versión recibida (la vigente es la que eligió dbt), su
        lote con sha256, su archivo y la ejecución que lo cargó; o su paso por cuarentena."""
        with db.connect() as conn:
            lineage = repo.order_lineage(conn, order_id)
        if lineage is None:
            raise HTTPException(status_code=404, detail="pedido no encontrado")
        return lineage

    @router.get(
        "/metrics/volumes",
        summary="Volúmenes y duraciones por día",
        response_model=list[DailyVolume],
        tags=["pipelines"],
    )
    def volumes(db: Db, days: Annotated[int, Query(ge=1, le=366)] = 30) -> list[dict[str, Any]]:
        with db.connect() as conn:
            return repo.daily_volumes(conn, days)

    @router.get(
        "/quality/summary",
        summary="Resumen por regla de calidad",
        response_model=list[QualitySummary],
        tags=["calidad"],
    )
    def quality_summary(
        db: Db, since: datetime | None = None, layer: Layer | None = None
    ) -> list[dict[str, Any]]:
        with db.connect() as conn:
            return repo.quality_summary(conn, since, layer)

    return router


async def _read_capped(request: Request, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail=f"el archivo supera {cap:,} bytes")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > cap:  # la cabecera puede mentir o faltar (envío por trozos)
            raise HTTPException(status_code=413, detail=f"el archivo supera {cap:,} bytes")
    return bytes(data)


def _preview(report: ValidationReport) -> ValidationPreview:
    return ValidationPreview(
        source=report.source,
        rows_read=report.rows_read,
        rows_valid=len(report.valid),
        rows_quarantined=len(report.quarantined),
        duplicates_dropped=report.duplicates_dropped,
        error_rate=round(report.error_rate, 4),
        rejected_reason=report.rejected_reason,
        quarantined=[
            QuarantinePreviewRow(
                row_number=q.row_number,
                record_key=None if q.record_key is None else str(q.record_key)[:100],
                errors=[RowIssue(rule=e.rule, field=e.field, message=e.message) for e in q.errors],
            )
            for q in report.quarantined[:MAX_LISTED_ROWS]
        ],
        failed_rules=[
            RuleOutcome(
                rule=r.rule,
                severity=r.severity.value,
                rows_evaluated=r.rows_evaluated,
                rows_failed=r.rows_failed,
            )
            for r in report.results
            if not r.passed
        ],
    )


def contracts_router() -> APIRouter:
    router = APIRouter(tags=["contratos"])

    @router.post(
        "/contracts/{source}/validate",
        response_model=ValidationPreview,
        summary="Prueba un archivo contra los contratos (no guarda nada)",
        openapi_extra={
            "requestBody": {
                "required": True,
                "description": f"El archivo tal cual, hasta {MAX_UPLOAD_BYTES:,} bytes.",
                "content": {
                    "text/csv": {"schema": {"type": "string"}, "example": _EXAMPLE_CSV},
                    "application/json": {
                        "schema": {"type": "object"},
                        "example": {"orders": [{"order_id": "O000231", "items": []}]},
                    },
                },
            }
        },
    )
    async def validate_file(
        request: Request, source: Literal["customers", "orders"]
    ) -> ValidationPreview:
        data = await _read_capped(request, MAX_UPLOAD_BYTES)
        if not data.strip():
            raise HTTPException(status_code=400, detail="el archivo está vacío")
        # Polars y json son trabajo de CPU: fuera del bucle de eventos.
        report = await run_in_threadpool(VALIDATORS[source], data)
        logger.info(
            "contract_preview",
            extra={"source": source, "bytes": len(data), "quarantined": len(report.quarantined)},
        )
        return _preview(report)

    return router


def admin_router() -> APIRouter:
    router = APIRouter(
        prefix="/v1/admin",
        tags=["administración"],
        dependencies=[Depends(require_key("admin_keys", hide_when_unconfigured=True))],
    )

    @router.post(
        "/pipelines/{name}/runs",
        summary="Dispara un flujo en Prefect",
        response_model=TriggerResponse,
        status_code=202,
    )
    def trigger_pipeline(
        request: Request, name: Literal["daily", "products"], body: TriggerRequest | None = None
    ) -> TriggerResponse:
        if body and body.full_refresh:
            raise HTTPException(status_code=400, detail="full_refresh no se permite desde la API")
        deployment = DEPLOYMENTS[name]
        try:
            flow_run_id, state = request.app.state.trigger(deployment, {"trigger": "api"})
        except Exception as exc:
            logger.exception("trigger_failed", extra={"deployment": deployment})
            raise HTTPException(
                status_code=503, detail="el orquestador no está disponible"
            ) from exc
        logger.info(
            "pipeline_triggered", extra={"deployment": deployment, "flow_run_id": str(flow_run_id)}
        )
        return TriggerResponse(deployment=deployment, flow_run_id=flow_run_id, state=state)

    return router


def _install_middleware_and_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[..., Any]) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/v1"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(OperationalError)
    async def db_unavailable(_: Request, exc: OperationalError) -> JSONResponse:
        logger.error("database_unavailable", extra={"error": type(exc.orig).__name__})
        return JSONResponse(status_code=503, content={"detail": "base de datos no disponible"})

    @app.exception_handler(SQLAlchemyError)
    async def db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        # Nunca se devuelve el SQL ni el mensaje del driver al cliente.
        logger.error("database_error", extra={"error": type(exc).__name__})
        return JSONResponse(status_code=500, content={"detail": "error interno"})

    @app.get("/health", summary="Vida del proceso", response_model=Health, tags=["salud"])
    def health() -> Health:
        return Health(status="ok")

    @app.get(
        "/health/ready",
        summary="Disponibilidad de la base de datos",
        response_model=Health,
        tags=["salud"],
    )
    def ready(request: Request) -> JSONResponse:
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "degraded", "database": "down"})
        return JSONResponse(content={"status": "ok", "database": "up"})


DESCRIPTION: Final = """
Indicadores de ventas, ejecuciones de pipelines, calidad de datos y linaje.

**Cómo probarla en un minuto**

1. Pulsa **Authorize** y pega una clave de lectura. Con `scripts/demo.ps1` se imprime al
   final; si no, es la primera de `DATAPULSE_API_KEYS` en tu `.env`.
2. `GET /v1/lineage/orders/{order_id}` con `O000065`: un pedido que llegó dos veces, con la
   versión vigente, su lote (sha256), el archivo y la ejecución que lo cargó.
3. `POST /v1/contracts/customers/validate` con el ejemplo del cuerpo: dice qué filas irían a
   cuarentena y por qué, sin guardar nada.
4. `GET /v1/quality/quarantine?field=email`: lo que los contratos apartaron de verdad.

Todo es de solo lectura: el usuario de base de datos de la API no puede escribir.
"""
TAGS: Final = [
    {"name": "indicadores", "description": "Ventas desde el modelo estrella (`marts`)."},
    {"name": "calidad", "description": "Reglas, cuarentena y frescura de cada fuente."},
    {"name": "linaje", "description": "De un indicador a su archivo de origen."},
    {"name": "contratos", "description": "Prueba un archivo contra los contratos, sin cargarlo."},
    {"name": "pipelines", "description": "Ejecuciones, tareas, lotes y volúmenes."},
    {"name": "salud", "description": "Vida y disponibilidad (sin clave)."},
    {"name": "administración", "description": "Disparo de flujos (clave de administración)."},
]


def create_app(
    settings: ApiSettings | None = None,
    engine: Engine | None = None,
    trigger: TriggerFn | None = None,
) -> FastAPI:
    settings = settings or ApiSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = engine or build_engine(get_settings().db)
        yield
        if engine is None:
            app.state.engine.dispose()

    app = FastAPI(
        title="DataPulse API",
        version="1.0.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.read_keys = KeyChecker(settings.keys)
    app.state.admin_keys = KeyChecker([settings.admin_key] if settings.admin_key else [])
    app.state.trigger = trigger or prefect_trigger
    _install_middleware_and_handlers(app)
    # La landing comprueba si la demo está en marcha en este equipo con GET /health. Solo
    # orígenes locales, solo GET y ninguna cabecera propia: un navegador no puede enviar
    # X-API-Key desde otro origen, así que /v1 sigue cerrado a otras páginas.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d{1,5})?$",
        allow_methods=["GET"],
        allow_headers=[],
    )

    read_auth = [Depends(require_key("read_keys"))]
    routers = (
        business_router(),
        paged_router(_paging_dependency(settings)),
        ops_router(settings),
        contracts_router(),
    )
    for router in routers:
        app.include_router(router, prefix="/v1", dependencies=read_auth)
    app.include_router(admin_router())
    return app
