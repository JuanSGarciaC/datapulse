"""Pruebas de extremo a extremo contra el stack desplegado (docker compose).

Caja negra: solo HTTP hacia los servicios publicados, sin importar código interno ni
tocar la base de datos. Recorre el camino completo:

    API admin → Prefect server → worker → mock-source (con fallos)
              → PostgreSQL → dbt → API → dashboard

Requiere el stack levantado y el flujo diario ejecutado una vez (lo hace el job de Docker
en CI). Variables: ``DATAPULSE_E2E_*`` (ver ``_settings``). Sin ellas se omiten, salvo en
CI, donde una omisión sería un falso verde y se convierte en fallo.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
import pytest

pytestmark = pytest.mark.e2e

POLL_TIMEOUT_S = 240


@dataclass(frozen=True)
class Stack:
    api: httpx.Client
    mock: httpx.Client
    dashboard_url: str
    prefect_url: str
    read: dict[str, str]
    admin: dict[str, str]
    mock_admin: dict[str, str]


def _settings() -> dict[str, str] | None:
    names = (
        "API_URL",
        "API_KEY",
        "ADMIN_KEY",
        "MOCK_URL",
        "MOCK_ADMIN_TOKEN",
        "DASHBOARD_URL",
        "PREFECT_URL",
    )
    values = {n: os.environ.get(f"DATAPULSE_E2E_{n}", "") for n in names}
    return values if all(values.values()) else None


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    cfg = _settings()
    if cfg is None:
        if os.environ.get("CI", "").lower() == "true":
            pytest.fail("Faltan variables DATAPULSE_E2E_* en CI")
        pytest.skip("stack no configurado (define DATAPULSE_E2E_*)")
    with (
        httpx.Client(base_url=cfg["API_URL"], timeout=30) as api,
        httpx.Client(base_url=cfg["MOCK_URL"], timeout=30) as mock,
    ):
        yield Stack(
            api=api,
            mock=mock,
            dashboard_url=cfg["DASHBOARD_URL"],
            prefect_url=cfg["PREFECT_URL"],
            read={"X-API-Key": cfg["API_KEY"]},
            admin={"X-API-Key": cfg["ADMIN_KEY"]},
            mock_admin={"X-Mock-Admin-Token": cfg["MOCK_ADMIN_TOKEN"]},
        )
        # Deja la fuente limpia aunque una prueba falle a mitad.
        mock.put("/_mock/faults", headers={"X-Mock-Admin-Token": cfg["MOCK_ADMIN_TOKEN"]}, json={})


def _latest_run(stack: Stack, flow_name: str) -> dict[str, Any] | None:
    page = stack.api.get(
        "/v1/pipeline-runs", params={"flow_name": flow_name, "limit": 1}, headers=stack.read
    ).json()
    items: list[dict[str, Any]] = page["items"]
    return items[0] if items else None


def _wait_for_run(stack: Stack, flow_name: str, after: datetime) -> dict[str, Any]:
    """Espera a que el worker termine una ejecución iniciada después de ``after``."""
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        run = _latest_run(stack, flow_name)
        if (
            run
            and datetime.fromisoformat(run["started_at"]) >= after
            and run["status"] != "running"
        ):
            detail: dict[str, Any] = stack.api.get(
                f"/v1/pipeline-runs/{run['run_id']}", headers=stack.read
            ).json()
            return detail
        time.sleep(3)
    pytest.fail(f"{flow_name} no terminó en {POLL_TIMEOUT_S}s")


# ------------------------------------------------------------------ salud y seguridad
def test_every_service_is_up(stack: Stack) -> None:
    assert stack.api.get("/health/ready").json() == {"status": "ok", "database": "up"}
    assert stack.mock.get("/health").json()["status"] == "ok"
    assert httpx.get(f"{stack.dashboard_url}/_stcore/health", timeout=10).text == "ok"
    assert httpx.get(f"{stack.prefect_url}/api/health", timeout=10).status_code == 200


def test_auth_is_enforced_across_the_stack(stack: Stack) -> None:
    assert stack.api.get("/v1/kpis/summary").status_code == 401
    assert stack.api.get("/v1/kpis/summary", headers={"X-API-Key": "x" * 32}).status_code == 401
    # Una clave de lectura no puede disparar pipelines.
    assert stack.api.post("/v1/admin/pipelines/products/runs", headers=stack.read).status_code in {
        401,
        403,
    }
    assert stack.mock.get("/_mock/state").status_code == 401
    # La documentación de errores no filtra trazas internas.
    body = stack.api.get("/v1/pipeline-runs/not-a-uuid", headers=stack.read).text
    assert "Traceback" not in body


# ------------------------------------------------------------------ respuesta conocida
def test_known_defects_are_visible_through_the_api(stack: Stack) -> None:
    """Los defectos del manifiesto llegan contados correctamente hasta la API."""
    summary = stack.api.get(
        "/v1/quality/summary", params={"layer": "pre_load"}, headers=stack.read
    ).json()
    failed = {(s["table_name"], s["rule_name"]): s["rows_failed"] for s in summary}
    assert failed["raw.customers", "not_null:email"] == 3
    assert failed["raw.customers", "format:email"] == 2
    assert failed["raw.orders", "type:items.quantity"] == 2
    batches = stack.api.get(
        "/v1/ingestion/batches", params={"source": "customers_csv"}, headers=stack.read
    ).json()
    assert batches["total"] == 3  # 4 archivos, pero el reenvío idéntico no crea lote


# ------------------------------------------------------------------ resiliencia de punta a punta
def test_admin_trigger_survives_faults_and_rate_limit_and_is_idempotent(stack: Stack) -> None:
    """Dispara la ingesta de productos por la API con la fuente fallando y limitando.

    La primera página (``skip=0``, que la carga incremental siempre pide) falla dos veces
    (503) y la fuente admite 3 peticiones cada 2 s (429 + ``Retry-After``). El cliente debe
    reintentar, respetar la espera y terminar. Como los productos ya se cargaron, la carga
    incremental solo relee la ventana de solapamiento y no inserta nada nuevo.
    """
    configured = stack.mock.put(
        "/_mock/faults",
        headers=stack.mock_admin,
        json={"fail_offsets": {"0": 2}, "rate_limit": 3, "rate_window_s": 2},
    )
    assert configured.status_code == 200
    before = _latest_run(stack, "ingest_products")
    started = datetime.now(UTC).replace(microsecond=0)

    trigger = stack.api.post("/v1/admin/pipelines/products/runs", headers=stack.admin)
    assert trigger.status_code == 202, trigger.text
    flow_run_id = trigger.json()["flow_run_id"]

    run = _wait_for_run(stack, "ingest_products", after=started)
    assert run["run_id"] != (before or {}).get("run_id")
    assert run["orchestrator_run_id"] == flow_run_id  # trazabilidad API → Prefect → ops
    assert run["trigger"] == "api"
    assert run["status"] in {"success", "partial"}, run["error_message"]
    assert run["rows_loaded"] == 0  # idempotente: nada nuevo desde la última marca de agua

    state = stack.mock.get("/_mock/state", headers=stack.mock_admin).json()
    # Los dos 503 de skip=0 ocurrieron de verdad (más los 429 que haya provocado el límite).
    assert state["faults_injected"] >= 2
    assert state["requests_seen"] > state["faults_injected"]  # y después hubo respuestas buenas


def test_dashboard_container_serves_the_app(stack: Stack) -> None:
    page = httpx.get(stack.dashboard_url, timeout=10)
    assert page.status_code == 200
    assert "<title>" in page.text
