"""Flujos de Prefect: orquestan los casos de uso de ``datapulse.ingestion``.

Diseño:

* La lógica vive en ``ingestion`` (probada sin orquestador). Aquí solo se
  decide **qué** se ejecuta, en qué orden y con qué política de reintentos.
* Reintentos en dos niveles: el cliente HTTP reintenta peticiones; Prefect
  reintenta **tareas** ante fallos transitorios (red, BD, fuente caída). Un
  contrato roto o un error de programación no se reintentan (``is_transient``).
* Cada intento queda en ``ops.task_runs`` con su número (``attempt``).
* Un ``RunRecorder`` por flujo → una fila en ``ops.pipeline_runs`` enlazada con
  el ``flow_run.id`` de Prefect (``orchestrator_run_id``).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import httpx2 as httpx
from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE
from prefect.client.schemas.objects import State, TaskRun
from prefect.runtime import flow_run, task_run
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine

from datapulse.config import get_settings
from datapulse.db.engine import build_engine
from datapulse.ingestion.load import BatchOutcome, ConcurrentIngestionError
from datapulse.ingestion.rest import ContractViolationError, ProductsClient, RetryPolicy
from datapulse.ingestion.service import (
    IngestionSettings,
    ingest_customers_file,
    ingest_orders_file,
    ingest_products,
)
from datapulse.observability.runs import RunRecorder, Trigger

logger = logging.getLogger(__name__)

FileSource = Literal["customers", "orders"]
PATTERNS: dict[FileSource, str] = {"customers": "*.csv", "orders": "*.json"}
# Errores que no se arreglan reintentando: reintentar solo retrasaría la alerta.
NON_RETRYABLE: tuple[type[BaseException], ...] = (
    ContractViolationError,
    ValueError,
    TypeError,
    KeyError,
    FileNotFoundError,
)


class FlowSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATAPULSE_FLOW_", env_file=".env", extra="ignore")

    task_retries: int = 2
    task_retry_delay_s: float = 10.0


def is_transient(_task: Any, _run: TaskRun, state: State[Any]) -> bool:
    """``retry_condition_fn``: reintenta solo fallos que pueden desaparecer solos."""
    try:
        state.result(raise_on_failure=True)
    except ConcurrentIngestionError:
        return True
    except NON_RETRYABLE:
        return False
    except Exception:
        return True
    return True


_FLOW_SETTINGS = FlowSettings()
_TASK_OPTIONS: dict[str, Any] = {
    "retries": _FLOW_SETTINGS.task_retries,
    "retry_delay_seconds": _FLOW_SETTINGS.task_retry_delay_s,
    "retry_condition_fn": is_transient,
    "cache_policy": NO_CACHE,  # los efectos están en la BD; Prefect no debe cachear
}


def _engine() -> Engine:
    return build_engine(get_settings().db)


def _orchestrator_id() -> uuid.UUID | None:
    run_id = flow_run.get_id()
    return uuid.UUID(run_id) if run_id else None


# ------------------------------------------------------------------ tareas
@task(name="ingest-file", **_TASK_OPTIONS)
def ingest_file_task(source: FileSource, path: Path, recorder: RunRecorder) -> dict[str, Any]:
    ingest = ingest_customers_file if source == "customers" else ingest_orders_file
    outcome: BatchOutcome = ingest(
        recorder.engine, recorder, path, attempt=task_run.get_run_count()
    )
    get_run_logger().info("%s → %s (cargadas %s)", path.name, outcome.status, outcome.rows_loaded)
    return outcome.as_dict()


@task(name="ingest-products-api", **_TASK_OPTIONS)
def ingest_products_task(base_url: str, recorder: RunRecorder) -> dict[str, Any]:
    cfg = IngestionSettings()
    with httpx.Client(base_url=base_url, timeout=cfg.timeout_s) as http:
        client = ProductsClient(
            http, page_size=cfg.page_size, policy=RetryPolicy(max_retries=cfg.max_retries)
        )
        outcome = ingest_products(
            recorder.engine,
            recorder,
            client,
            base_url=base_url,
            overlap=timedelta(minutes=cfg.overlap_minutes),
            attempt=task_run.get_run_count(),
        )
    get_run_logger().info("productos → %s (cargados %s)", outcome.status, outcome.rows_loaded)
    return outcome.as_dict()


# ------------------------------------------------------------------ flujos
def discover(landing_dir: Path, source: FileSource) -> list[Path]:
    """Archivos de la fuente en la zona de aterrizaje, en orden de nombre (= cronológico)."""
    folder = landing_dir / source
    return sorted(folder.glob(PATTERNS[source]), key=lambda p: p.name) if folder.is_dir() else []


@flow(name="ingest-files", log_prints=False)
def ingest_files_flow(
    source: FileSource, landing_dir: str | None = None, trigger: Trigger = "manual"
) -> list[dict[str, Any]]:
    root = Path(landing_dir) if landing_dir else IngestionSettings().landing_dir
    paths = discover(root, source)
    engine = _engine()
    try:
        with RunRecorder(
            engine,
            f"ingest_{source}",
            source_name=f"{source}_{'csv' if source == 'customers' else 'json'}",
            trigger=trigger,
            params={"landing_dir": str(root), "files": [p.name for p in paths]},
            orchestrator_run_id=_orchestrator_id(),
        ) as recorder:
            # Secuencial a propósito: el orden de los archivos importa (versiones).
            return [ingest_file_task(source, path, recorder) for path in paths]
    finally:
        engine.dispose()


@flow(name="ingest-products")
def ingest_products_flow(
    base_url: str | None = None, trigger: Trigger = "manual"
) -> dict[str, Any]:
    url = base_url or IngestionSettings().products_api_url
    engine = _engine()
    try:
        with RunRecorder(
            engine,
            "ingest_products",
            source_name="products_api",
            trigger=trigger,
            params={"base_url": url},
            orchestrator_run_id=_orchestrator_id(),
        ) as recorder:
            result: dict[str, Any] = ingest_products_task(url, recorder)
            return result
    finally:
        engine.dispose()


@flow(name="daily-pipeline")
def daily_pipeline(
    landing_dir: str | None = None,
    base_url: str | None = None,
    trigger: Trigger = "manual",
    transform: bool = True,
) -> dict[str, Any]:
    """Clientes → pedidos → productos → transformación (dbt, fase 5).

    Si una fuente falla, las demás se intentan igual (``return_state``) y el flujo
    termina en fallo al final: un CSV roto no debe impedir cargar la API.
    """
    states: dict[str, State[Any]] = {
        "customers": ingest_files_flow(
            "customers", landing_dir=landing_dir, trigger=trigger, return_state=True
        ),
        "orders": ingest_files_flow(
            "orders", landing_dir=landing_dir, trigger=trigger, return_state=True
        ),
        "products": ingest_products_flow(base_url=base_url, trigger=trigger, return_state=True),
    }
    summary: dict[str, Any] = {name: state.type.value for name, state in states.items()}
    failed = [name for name, state in states.items() if state.is_failed()]
    if transform and not failed:
        summary["transform"] = run_transform(trigger=trigger)
    if failed:
        raise RuntimeError(f"fuentes con fallo: {failed}")
    return summary


def run_transform(trigger: Trigger) -> str:  # pragma: no cover - se sustituye en la fase 5
    return "pendiente (fase 5)"


def seed_landing(samples: Path, landing: Path) -> list[Path]:
    """Copia los datos de ejemplo a la zona de aterrizaje (para demos y pruebas E2E)."""
    copied = []
    for source, pattern in PATTERNS.items():
        target = landing / source
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted((samples / source).glob(pattern)):
            copied.append(Path(shutil.copy2(path, target / path.name)))
    return copied
