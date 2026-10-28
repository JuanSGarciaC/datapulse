"""API contra un almacén real (ingesta + dbt), conectada con el rol de solo lectura."""

from __future__ import annotations

import random
import secrets
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, event, text

from datapulse.api.app import create_app
from datapulse.api.settings import ApiSettings
from datapulse.ingestion.rest import ProductsClient
from datapulse.ingestion.service import ingest_customers_file, ingest_orders_file, ingest_products
from datapulse.mock_source.app import MockSettings
from datapulse.mock_source.app import create_app as create_mock
from datapulse.observability.runs import RunRecorder
from datapulse.transform.dbt import record_test_results, run_build
from tests.conftest import ROOT, alembic_config

pytestmark = pytest.mark.integration
SAMPLES = ROOT / "data/samples"
KEY = secrets.token_urlsafe(24)  # generada en cada ejecución
AUTH = {"X-API-Key": KEY}


@pytest.fixture(scope="module")
def warehouse(admin_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Engine:
    """Construye el almacén una vez por módulo: migraciones, ingesta y dbt."""
    cfg = alembic_config(admin_engine)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    url = admin_engine.url
    with pytest.MonkeyPatch.context() as mp:
        for key, value in {
            "DATAPULSE_DB_HOST": url.host,
            "DATAPULSE_DB_PORT": url.port,
            "DATAPULSE_DB_NAME": url.database,
            "DATAPULSE_DBT_DB_USER": url.username,
            "DATAPULSE_DBT_DB_PASSWORD": url.password,
            "DATAPULSE_DBT_ROLE": "dp_transform",
            "DATAPULSE_DBT_PROJECT_DIR": ROOT / "dbt",
            "DATAPULSE_DBT_TARGET_DIR": tmp_path_factory.mktemp("dbt"),
        }.items():
            mp.setenv(key, str(value))
        mock = create_mock(
            MockSettings(_env_file=None, catalog_path=SAMPLES / "products/products_catalog.json")
        )
        with (
            RunRecorder(admin_engine, "ingest_all", trigger="test") as run,
            TestClient(mock) as api,
        ):
            for path in sorted((SAMPLES / "customers").glob("*.csv")):
                ingest_customers_file(admin_engine, run, path)
            for path in sorted((SAMPLES / "orders").glob("*.json")):
                ingest_orders_file(admin_engine, run, path)
            client = ProductsClient(api, sleep=lambda _: None, rng=random.Random(0))
            ingest_products(admin_engine, run, client, base_url="http://mock")
        with RunRecorder(admin_engine, "transform", trigger="test") as run:
            outcome = run_build()
            record_test_results(admin_engine, run.run_id, outcome)
            assert outcome.success
    return admin_engine


@pytest.fixture(scope="module")
def reader(warehouse: Engine) -> Iterator[Engine]:
    """La API se prueba con los permisos reales de producción (dp_reader)."""
    engine = create_engine(warehouse.url)

    @event.listens_for(engine, "connect")
    def _as_reader(dbapi_conn: Any, _: Any) -> None:
        with dbapi_conn.cursor() as cur:
            cur.execute("SET ROLE dp_reader")
        # Confirmar: SET ROLE dentro de la transacción implícita se perdería con el
        # ROLLBACK que hace el pool al devolver la conexión.
        dbapi_conn.commit()

    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def api(reader: Engine) -> Iterator[TestClient]:
    settings = ApiSettings(_env_file=None, keys=[SecretStr(KEY)])
    with TestClient(create_app(settings, engine=reader)) as client:
        yield client


def sql(engine: Engine, query: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(query)).one()


def test_kpis_match_an_independent_sql_computation(api: TestClient, warehouse: Engine) -> None:
    body = api.get("/v1/kpis/summary", headers=AUTH).json()
    expected = sql(
        warehouse,
        "SELECT sum(line_amount), count(DISTINCT order_id), sum(quantity) "
        "FROM marts.fct_order_items WHERE status NOT IN ('cancelled', 'returned')",
    )
    assert Decimal(body["revenue"]) == expected[0]
    assert body["orders"] == expected[1]
    assert body["units"] == expected[2]
    assert Decimal(body["average_order_value"]) == (expected[0] / expected[1]).quantize(
        Decimal("0.01")
    )
    assert isinstance(body["revenue"], str)  # importes como texto decimal, sin coma flotante


def test_date_filters_partition_the_total(api: TestClient) -> None:
    total = Decimal(api.get("/v1/kpis/summary", headers=AUTH).json()["revenue"])
    parts = [
        Decimal(
            api.get(
                "/v1/kpis/summary", params={"date_from": day, "date_to": day}, headers=AUTH
            ).json()["revenue"]
        )
        for day in ("2026-01-01", "2026-01-02", "2026-01-03")
    ]
    assert sum(parts) == total


def test_daily_sales_paginates_with_a_stable_total(api: TestClient) -> None:
    first = api.get("/v1/sales/daily", params={"limit": 10}, headers=AUTH).json()
    rest = api.get("/v1/sales/daily", params={"limit": 500, "offset": 10}, headers=AUTH).json()
    beyond = api.get("/v1/sales/daily", params={"limit": 10, "offset": 9999}, headers=AUTH).json()
    assert first["total"] == rest["total"] == beyond["total"] > 10
    assert len(first["items"]) + len(rest["items"]) == first["total"]
    assert beyond["items"] == []
    category = first["items"][0]["category"]
    filtered = api.get("/v1/sales/daily", params={"category": category}, headers=AUTH).json()
    assert {i["category"] for i in filtered["items"]} == {category}


def test_category_and_top_products(api: TestClient) -> None:
    categories = api.get("/v1/sales/by-category", headers=AUTH).json()
    revenues = [Decimal(c["revenue"]) for c in categories]
    assert revenues == sorted(revenues, reverse=True)
    top = api.get("/v1/products/top", params={"metric": "units", "limit": 5}, headers=AUTH).json()
    assert len(top) == 5
    assert [p["units"] for p in top] == sorted((p["units"] for p in top), reverse=True)


def test_pipeline_runs_and_detail(api: TestClient) -> None:
    runs = api.get("/v1/pipeline-runs", headers=AUTH).json()
    assert {r["flow_name"] for r in runs["items"]} == {"ingest_all", "transform"}
    run_id = next(r["run_id"] for r in runs["items"] if r["flow_name"] == "ingest_all")
    detail = api.get(f"/v1/pipeline-runs/{run_id}", headers=AUTH).json()
    assert len(detail["tasks"]) == 8  # 4 CSV + 3 JSON + API
    assert detail["quality_failures"] > 0
    assert (
        api.get("/v1/pipeline-runs/00000000-0000-0000-0000-000000000000", headers=AUTH).status_code
        == 404
    )
    partial = api.get("/v1/pipeline-runs", params={"status": "partial"}, headers=AUTH).json()
    assert [r["flow_name"] for r in partial["items"]] == ["ingest_all"]


def test_quality_endpoints(api: TestClient) -> None:
    summary = api.get("/v1/quality/summary", headers=AUTH).json()
    by_rule = {(s["table_name"], s["rule_name"]): s for s in summary}
    assert by_rule["raw.customers", "not_null:email"]["rows_failed"] == 3
    assert by_rule["raw.orders", "type:items.quantity"]["rows_failed"] == 2
    post = api.get("/v1/quality/summary", params={"layer": "post_transform"}, headers=AUTH).json()
    assert {s["severity"] for s in post if not s["last_passed"]} == {"warn"}
    failed = api.get(
        "/v1/quality/results", params={"passed": "false", "limit": 500}, headers=AUTH
    ).json()
    assert failed["total"] == len(failed["items"]) > 0
    assert all(not r["passed"] for r in failed["items"])


def test_batches_and_volumes_for_lineage(api: TestClient) -> None:
    batches = api.get(
        "/v1/ingestion/batches", params={"source": "customers_csv"}, headers=AUTH
    ).json()
    assert batches["total"] == 3  # el reenvío idéntico no crea lote
    assert all(len(b["content_sha256"]) == 64 for b in batches["items"])
    volumes = api.get("/v1/metrics/volumes", params={"days": 7}, headers=AUTH).json()
    assert {v["flow_name"] for v in volumes} == {"ingest_all", "transform"}
    assert all(v["avg_duration_s"] is not None for v in volumes)


def test_readiness_with_a_real_database(api: TestClient) -> None:
    assert api.get("/health/ready").json() == {"status": "ok", "database": "up"}


def test_the_api_role_cannot_read_raw_even_if_asked(reader: Engine) -> None:
    from sqlalchemy.exc import ProgrammingError  # noqa: PLC0415

    for _ in range(3):  # varias conexiones del pool: el rol no se pierde al reutilizarlas
        with reader.connect() as conn:
            assert conn.execute(text("SELECT current_user")).scalar_one() == "dp_reader"

    with pytest.raises(ProgrammingError, match="permission denied"), reader.connect() as conn:
        conn.execute(text("SELECT email FROM raw.customers LIMIT 1"))
