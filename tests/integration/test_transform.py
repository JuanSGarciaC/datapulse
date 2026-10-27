"""dbt contra PostgreSQL real: modelos, SCD2, conciliación e incremental == full refresh."""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from datapulse.ingestion.rest import ProductsClient
from datapulse.ingestion.service import (
    ingest_customers_file,
    ingest_orders_file,
    ingest_products,
)
from datapulse.mock_source.app import MockSettings, create_app
from datapulse.observability.runs import RunRecorder
from datapulse.transform.dbt import TransformOutcome, record_test_results, run_build
from tests.conftest import ROOT

pytestmark = pytest.mark.integration
SAMPLES = ROOT / "data/samples"
DAYS = ("2026-01-01", "2026-01-02", "2026-01-03")


@pytest.fixture
def dbt_env(migrated_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Engine:
    """dbt usa el usuario de pruebas pero actúa como dp_transform (igual que en producción)."""
    url = migrated_db.url
    for key, value in {
        "DATAPULSE_DB_HOST": url.host,
        "DATAPULSE_DB_PORT": url.port,
        "DATAPULSE_DB_NAME": url.database,
        "DATAPULSE_DBT_DB_USER": url.username,
        "DATAPULSE_DBT_DB_PASSWORD": url.password,
        "DATAPULSE_DBT_ROLE": "dp_transform",
        "DATAPULSE_DBT_PROJECT_DIR": ROOT / "dbt",
        "DATAPULSE_DBT_TARGET_DIR": tmp_path / "target",
    }.items():
        monkeypatch.setenv(key, str(value))
    return migrated_db


@pytest.fixture
def api() -> Iterator[TestClient]:
    settings = MockSettings(
        _env_file=None, catalog_path=SAMPLES / "products/products_catalog.json", admin_token="t"
    )
    with TestClient(create_app(settings)) as client:
        yield client


def ingest_day(engine: Engine, api: TestClient, day_index: int) -> None:
    day = DAYS[day_index]
    with RunRecorder(engine, "test_ingest", trigger="test") as run:
        for path in sorted((SAMPLES / "customers").glob(f"customers_{day}*.csv")):
            ingest_customers_file(engine, run, path)
        ingest_orders_file(engine, run, SAMPLES / f"orders/orders_batch_{day_index + 1:03d}.json")
        api.put(
            "/_mock/clock", headers={"X-Mock-Admin-Token": "t"}, json={"as_of": f"{day}T23:59:59Z"}
        )
        client = ProductsClient(api, sleep=lambda _: None, rng=random.Random(0))
        ingest_products(engine, run, client, base_url="http://mock")


def build(full_refresh: bool = False) -> TransformOutcome:
    outcome = run_build(full_refresh=full_refresh)
    assert outcome.success, [n for n in outcome.nodes if n.status in {"error", "fail"}]
    return outcome


def q(engine: Engine, sql: str) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(text(sql)).all())


def one(engine: Engine, sql: str) -> Any:
    return q(engine, sql)[0][0]


@pytest.fixture
def loaded(dbt_env: Engine, api: TestClient) -> Engine:
    for i in range(3):
        ingest_day(dbt_env, api, i)
    return dbt_env


def test_build_passes_and_marts_have_the_expected_grain(loaded: Engine) -> None:
    outcome = build()
    assert outcome.count("model") == 10
    assert outcome.count("test", "fail") == 0
    assert outcome.count("test", "warn") == 2  # referencias huérfanas: aviso, no error
    assert one(loaded, "SELECT count(*) FROM marts.dim_customer WHERE is_current") == one(
        loaded, "SELECT count(DISTINCT customer_id) FROM raw.customers"
    )
    assert one(loaded, "SELECT count(*) FROM marts.dim_product") == one(
        loaded, "SELECT count(DISTINCT product_id) FROM raw.products"
    )
    # Una fila por línea de la versión vigente de cada pedido.
    assert one(loaded, "SELECT count(DISTINCT order_id) FROM marts.fct_order_items") == one(
        loaded, "SELECT count(DISTINCT order_id) FROM raw.orders"
    )


def test_orphan_warnings_match_lines_without_a_dimension(loaded: Engine) -> None:
    outcome = build()
    warns = {n.name.split("__")[0]: n.failures for n in outcome.nodes if n.status == "warn"}
    expected_customers = one(
        loaded,
        "SELECT count(*) FROM marts.fct_order_items f "
        "WHERE NOT EXISTS (SELECT 1 FROM raw.customers c WHERE c.customer_id = f.customer_id)",
    )
    expected_products = one(
        loaded,
        "SELECT count(*) FROM marts.fct_order_items f "
        "WHERE NOT EXISTS (SELECT 1 FROM raw.products p WHERE p.product_id = f.product_id)",
    )
    assert warns["relationships_fct_order_items_customer_id"] == expected_customers
    assert warns["relationships_fct_order_items_product_id"] == expected_products
    # Incluye los huérfanos inyectados (C99999 y 9999) y los que dejó la cuarentena.
    assert expected_customers >= 2
    assert expected_products >= 2


def test_late_updates_win_and_cancellations_leave_revenue(loaded: Engine) -> None:
    build()
    rows = q(
        loaded,
        "SELECT o.order_id, o.status FROM staging.stg_orders o WHERE o.versions_received = 2",
    )
    assert len(rows) == 5
    facts = dict(
        q(
            loaded,
            "SELECT DISTINCT order_id, status FROM marts.fct_order_items WHERE order_id IN "
            "(SELECT order_id FROM staging.stg_orders WHERE versions_received = 2)",
        )
    )
    assert facts == dict(rows)
    assert all(status in {"returned", "cancelled"} for status in facts.values())
    assert (
        one(
            loaded,
            "SELECT count(*) FROM marts.fct_order_items WHERE NOT is_revenue AND status NOT IN "
            "('cancelled', 'returned')",
        )
        == 0
    )


def test_scd2_point_in_time_join(loaded: Engine) -> None:
    build()
    changed = one(
        loaded,
        "SELECT count(DISTINCT customer_id) FROM marts.dim_customer WHERE version_number > 1",
    )
    assert changed > 0
    # Un pedido del día 1 de un cliente que cambió el día 2 apunta a la versión 1.
    mismatches = one(
        loaded,
        """
        SELECT count(*) FROM marts.fct_order_items f
        JOIN marts.dim_customer c ON c.customer_sk = f.customer_sk
        WHERE NOT (f.order_ts >= c.valid_from AND (c.valid_to IS NULL OR f.order_ts < c.valid_to))
        """,
    )
    assert mismatches == 0
    assert (
        one(
            loaded,
            "SELECT count(*) FROM marts.fct_order_items f JOIN marts.dim_customer c "
            "USING (customer_sk) WHERE f.order_date = '2026-01-01' AND c.version_number = 1",
        )
        > 0
    )


def test_incremental_day_by_day_equals_full_refresh(dbt_env: Engine, api: TestClient) -> None:
    for i in range(3):
        ingest_day(dbt_env, api, i)
        build()  # incremental tras cada día
    incremental = q(dbt_env, "SELECT * FROM marts.fct_order_items ORDER BY order_id, line_no")
    daily_inc = q(dbt_env, "SELECT * FROM marts.fct_daily_sales ORDER BY 1, 2")

    build(full_refresh=True)
    full = q(dbt_env, "SELECT * FROM marts.fct_order_items ORDER BY order_id, line_no")
    daily_full = q(dbt_env, "SELECT * FROM marts.fct_daily_sales ORDER BY 1, 2")

    def strip(rows: list[Any]) -> list[tuple[Any, ...]]:
        # _ingested_at y _batch_id son de la versión vigente: iguales en ambos caminos.
        return [tuple(r) for r in rows]

    assert len(incremental) == len(full) > 0
    assert strip(incremental) == strip(full)
    assert daily_inc == daily_full


def test_results_are_recorded_and_marts_are_readable_by_the_api_role(loaded: Engine) -> None:
    outcome = build()
    run_id = uuid.uuid4()
    with loaded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops.pipeline_runs (run_id, flow_name, status) "
                "VALUES (:r, 't', 'success')"
            ),
            {"r": run_id},
        )
    recorded = record_test_results(loaded, run_id, outcome)
    assert recorded == outcome.count("test")
    warn_rows = q(
        loaded,
        "SELECT table_name, severity FROM ops.quality_results "
        "WHERE layer = 'post_transform' AND NOT passed",
    )
    assert {tuple(r) for r in warn_rows} == {("marts.fct_order_items", "warn")}
    for table in ("dim_customer", "dim_product", "dim_date", "fct_order_items", "fct_daily_sales"):
        assert one(loaded, f"SELECT has_table_privilege('dp_reader', 'marts.{table}', 'SELECT')")
        assert not one(
            loaded, f"SELECT has_table_privilege('dp_reader', 'marts.{table}', 'INSERT')"
        )
    # El correo en claro nunca llega a los marts.
    assert (
        one(
            loaded,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'marts' AND column_name = 'email'",
        )
        == 0
    )
