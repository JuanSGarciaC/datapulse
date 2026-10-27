"""Orquestación con Prefect (servidor efímero real) contra PostgreSQL y la API simulada."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
import uvicorn
from prefect.testing.utilities import prefect_test_harness
from sqlalchemy import Engine, text

from datapulse.flows.pipeline import daily_pipeline, ingest_products_flow, seed_landing
from datapulse.mock_source.app import MockSettings, create_app
from tests.conftest import ROOT

pytestmark = pytest.mark.integration
SAMPLES = ROOT / "data/samples"
ADMIN = {"X-Mock-Admin-Token": "t"}


@pytest.fixture(scope="module")
def prefect() -> Iterator[None]:
    with prefect_test_harness(server_startup_timeout=120):
        yield


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def mock_url() -> Iterator[str]:
    """API simulada real (uvicorn en un hilo): el flujo usa HTTP de verdad."""
    port = _free_port()
    settings = MockSettings(
        _env_file=None, catalog_path=SAMPLES / "products/products_catalog.json", admin_token="t"
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover
            raise RuntimeError("la API simulada no arrancó")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def db_env(migrated_db: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    """Los flujos construyen su engine desde DATAPULSE_DB_*: se apunta a la BD de pruebas."""
    url = migrated_db.url
    for key, value in {
        "HOST": url.host,
        "PORT": url.port,
        "NAME": url.database,
        "USER": url.username,
        "PASSWORD": url.password,
    }.items():
        monkeypatch.setenv(f"DATAPULSE_DB_{key}", str(value))
    from datapulse.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    return migrated_db


@pytest.fixture
def landing(tmp_path: Path) -> str:
    seed_landing(SAMPLES, tmp_path)
    return str(tmp_path)


def rows(engine: Engine, sql: str) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(text(sql)).all())


def test_daily_pipeline_loads_every_source(
    prefect: None, db_env: Engine, mock_url: str, landing: str
) -> None:
    summary = daily_pipeline(
        landing_dir=landing, base_url=mock_url, transform=False, trigger="test"
    )
    assert summary == {"customers": "COMPLETED", "orders": "COMPLETED", "products": "COMPLETED"}
    runs = rows(db_env, "SELECT flow_name, status, orchestrator_run_id FROM ops.pipeline_runs")
    assert {r.flow_name for r in runs} == {"ingest_customers", "ingest_orders", "ingest_products"}
    assert all(r.orchestrator_run_id is not None for r in runs)  # enlazado con Prefect
    assert rows(db_env, "SELECT count(*) FROM raw.orders")[0][0] == 448


def test_task_retry_recovers_from_a_source_outage(
    prefect: None,
    db_env: Engine,
    mock_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # El cliente HTTP solo reintenta 1 vez; la fuente falla 3 veces seguidas en skip=0.
    # 1.er intento de la tarea: 2 peticiones fallidas → la tarea falla.
    # 2.º intento (Prefect): 1 fallo más y luego éxito.
    monkeypatch.setenv("DATAPULSE_INGEST_MAX_RETRIES", "1")
    httpx.put(f"{mock_url}/_mock/faults", headers=ADMIN, json={"fail_offsets": {"0": 3}})

    result = ingest_products_flow(base_url=mock_url, trigger="test")

    assert result["status"] == "success"
    attempts = rows(
        db_env,
        "SELECT attempt, status FROM ops.task_runs WHERE task_name = 'ingest:products_api' "
        "ORDER BY attempt",
    )
    assert [(a.attempt, a.status) for a in attempts] == [(1, "failed"), (2, "success")]
    assert rows(db_env, "SELECT status FROM ops.pipeline_runs")[0][0] == "partial"


def test_failed_source_does_not_block_the_others_and_rerun_recovers(
    prefect: None, db_env: Engine, mock_url: str, landing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATAPULSE_INGEST_MAX_RETRIES", "0")
    down = "http://127.0.0.1:9"  # nada escucha aquí
    state = daily_pipeline(
        landing_dir=landing, base_url=down, transform=False, trigger="test", return_state=True
    )
    assert state.is_failed()
    assert "products" in str(state.message)
    # Clientes y pedidos sí se cargaron pese a la caída de la API.
    assert rows(db_env, "SELECT count(*) FROM raw.customers")[0][0] == 263
    assert rows(db_env, "SELECT count(*) FROM raw.products")[0][0] == 0
    statuses = dict(rows(db_env, "SELECT flow_name, status FROM ops.pipeline_runs"))
    assert statuses["ingest_products"] == "failed"

    # Recuperación: se repite todo con la fuente disponible; nada se duplica.
    summary = daily_pipeline(
        landing_dir=landing, base_url=mock_url, transform=False, trigger="test"
    )
    assert set(summary.values()) == {"COMPLETED"}
    assert rows(db_env, "SELECT count(*) FROM raw.customers")[0][0] == 263
    assert rows(db_env, "SELECT count(DISTINCT product_id) FROM raw.products")[0][0] == 197
