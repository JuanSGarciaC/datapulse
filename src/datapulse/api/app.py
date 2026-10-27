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
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
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
    Page,
    PipelineRun,
    PipelineRunDetail,
    QualityResult,
    QualitySummary,
    TopProduct,
    TriggerRequest,
    TriggerResponse,
)
from datapulse.api.security import KeyChecker, require_key
from datapulse.api.settings import ApiSettings
from datapulse.config import get_settings
from datapulse.db.engine import build_engine

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
DateFrom = Annotated[date | None, Query(description="Desde (incluido), AAAA-MM-DD")]
DateTo = Annotated[date | None, Query(description="Hasta (incluido), AAAA-MM-DD")]
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

    @router.get("/kpis/summary", response_model=KpiSummary)
    def kpis(db: Db, date_from: DateFrom = None, date_to: DateTo = None) -> dict[str, Any]:
        _check_range(date_from, date_to)
        with db.connect() as conn:
            return repo.kpi_summary(conn, date_from, date_to)

    @router.get("/sales/by-category", response_model=list[CategorySales])
    def by_category(
        db: Db, date_from: DateFrom = None, date_to: DateTo = None
    ) -> list[dict[str, Any]]:
        _check_range(date_from, date_to)
        with db.connect() as conn:
            return repo.sales_by_category(conn, date_from, date_to)

    @router.get("/products/top", response_model=list[TopProduct])
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

    @router.get("/sales/daily", response_model=Page[DailySales], tags=["indicadores"])
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

    @router.get("/pipeline-runs", response_model=Page[PipelineRun], tags=["pipelines"])
    def runs(
        db: Db, page: Paging, status: RunStatus | None = None, flow_name: ShortText = None
    ) -> dict[str, Any]:
        with db.connect() as conn:
            items, total = repo.pipeline_runs(
                conn, status=status, flow_name=flow_name, limit=page[0], offset=page[1]
            )
        return _page(items, total, page)

    @router.get("/ingestion/batches", response_model=Page[Batch], tags=["pipelines"])
    def batches(
        db: Db, page: Paging, source: ShortText = None, status: BatchStatus | None = None
    ) -> dict[str, Any]:
        with db.connect() as conn:
            items, total = repo.batches(
                conn, source=source, status=status, limit=page[0], offset=page[1]
            )
        return _page(items, total, page)

    @router.get("/quality/results", response_model=Page[QualityResult], tags=["calidad"])
    def quality_results(
        db: Db, page: Paging, table_name: ShortText = None, passed: bool | None = None
    ) -> dict[str, Any]:
        with db.connect() as conn:
            items, total = repo.quality_results(
                conn, table_name=table_name, passed=passed, limit=page[0], offset=page[1]
            )
        return _page(items, total, page)

    return router


def ops_router() -> APIRouter:
    router = APIRouter()

    @router.get("/pipeline-runs/{run_id}", response_model=PipelineRunDetail, tags=["pipelines"])
    def run_detail(db: Db, run_id: uuid.UUID) -> dict[str, Any]:
        with db.connect() as conn:
            run = repo.pipeline_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="ejecución no encontrada")
        return run

    @router.get("/metrics/volumes", response_model=list[DailyVolume], tags=["pipelines"])
    def volumes(db: Db, days: Annotated[int, Query(ge=1, le=366)] = 30) -> list[dict[str, Any]]:
        with db.connect() as conn:
            return repo.daily_volumes(conn, days)

    @router.get("/quality/summary", response_model=list[QualitySummary], tags=["calidad"])
    def quality_summary(
        db: Db, since: datetime | None = None, layer: Layer | None = None
    ) -> list[dict[str, Any]]:
        with db.connect() as conn:
            return repo.quality_summary(conn, since, layer)

    return router


def admin_router() -> APIRouter:
    router = APIRouter(
        prefix="/v1/admin",
        tags=["administración"],
        dependencies=[Depends(require_key("admin_keys", hide_when_unconfigured=True))],
    )

    @router.post("/pipelines/{name}/runs", response_model=TriggerResponse, status_code=202)
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

    @app.get("/health", response_model=Health, tags=["salud"])
    def health() -> Health:
        return Health(status="ok")

    @app.get("/health/ready", response_model=Health, tags=["salud"])
    def ready(request: Request) -> JSONResponse:
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "degraded", "database": "down"})
        return JSONResponse(content={"status": "ok", "database": "up"})


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
        description="Indicadores de ventas, ejecuciones de pipelines y calidad de datos.",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.read_keys = KeyChecker(settings.keys)
    app.state.admin_keys = KeyChecker([settings.admin_key] if settings.admin_key else [])
    app.state.trigger = trigger or prefect_trigger
    _install_middleware_and_handlers(app)

    read_auth = [Depends(require_key("read_keys"))]
    for router in (business_router(), paged_router(_paging_dependency(settings)), ops_router()):
        app.include_router(router, prefix="/v1", dependencies=read_auth)
    app.include_router(admin_router())
    return app
