"""Ingesta de punta a punta contra PostgreSQL real, con el rol ``dp_pipeline``.

La prueba central compara lo que detecta el pipeline con los defectos que el
generador declaró en ``manifest.json`` (respuesta conocida): si una regla deja
de detectar algo, o detecta de más, esta prueba falla.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text

from datapulse.ingestion import load as load_module
from datapulse.ingestion.load import ConcurrentIngestionError
from datapulse.ingestion.rest import ProductsClient
from datapulse.ingestion.service import (
    ingest_customers_file,
    ingest_orders_file,
    ingest_products,
)
from datapulse.mock_source.app import MockSettings, create_app
from datapulse.observability.runs import RunRecorder
from tests.conftest import ROOT

pytestmark = pytest.mark.integration

SAMPLES = ROOT / "data/samples"
MANIFEST = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
DAY_END = "T23:59:59Z"
COUNTS = (
    "SELECT count(*) FROM raw.customers",
    "SELECT count(*) FROM raw.orders",
    "SELECT count(*) FROM raw.quarantine",
    "SELECT count(*) FROM ops.ingestion_registry",
)


@pytest.fixture
def pipeline_engine(migrated_db: Engine) -> Iterator[Engine]:
    """Mismo servidor, pero cada conexión actúa como ``dp_pipeline`` (mínimo privilegio)."""
    engine = create_engine(migrated_db.url)

    @event.listens_for(engine, "connect")
    def _as_pipeline(dbapi_conn: Any, _: Any) -> None:
        with dbapi_conn.cursor() as cur:
            cur.execute("SET ROLE dp_pipeline")

    yield engine
    engine.dispose()


@pytest.fixture
def mock_api() -> Iterator[TestClient]:
    settings = MockSettings(
        _env_file=None,
        catalog_path=SAMPLES / "products/products_catalog.json",
        admin_token="t",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def set_clock(api: TestClient, as_of: str | None) -> None:
    api.put("/_mock/clock", headers={"X-Mock-Admin-Token": "t"}, json={"as_of": as_of})


def products_client(api: TestClient) -> ProductsClient:
    return ProductsClient(api, sleep=lambda _: None, rng=random.Random(0))


def ingest_all_files(engine: Engine) -> list[Any]:
    outcomes = []
    with RunRecorder(engine, "test_customers", trigger="test") as run:
        for path in sorted((SAMPLES / "customers").glob("*.csv")):
            outcomes.append(ingest_customers_file(engine, run, path))
    with RunRecorder(engine, "test_orders", trigger="test") as run:
        for path in sorted((SAMPLES / "orders").glob("*.json")):
            outcomes.append(ingest_orders_file(engine, run, path))
    return outcomes


def run_products(engine: Engine, api: TestClient) -> Any:
    with RunRecorder(engine, "test_products", trigger="test") as run:
        return ingest_products(engine, run, products_client(api), base_url="http://mock")


def scalar(engine: Engine, sql: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


# ------------------------------------------------------------------ respuesta conocida
def test_pipeline_detects_exactly_the_manifest_defects(
    pipeline_engine: Engine, migrated_db: Engine, mock_api: TestClient
) -> None:
    ingest_all_files(pipeline_engine)
    set_clock(mock_api, MANIFEST["config"]["start"] + DAY_END)
    run_products(pipeline_engine, mock_api)

    defects = Counter((d["source"], d["kind"]) for d in MANIFEST["defects"])
    with migrated_db.connect() as conn:
        failures: dict[str, int] = dict(
            conn.execute(
                text(
                    "SELECT table_name || '|' || rule_name, sum(rows_failed) "
                    "FROM ops.quality_results GROUP BY 1"
                )
            ).all()
        )
        quarantined: dict[str, int] = dict(
            conn.execute(text("SELECT source_name, count(*) FROM raw.quarantine GROUP BY 1")).all()
        )

    # Clientes
    assert failures["raw.customers|not_null:email"] == 3
    assert failures["raw.customers|format:email"] == defects["customers_csv", "invalid_format"]
    assert failures["raw.customers|type:signup_date"] == defects["customers_csv", "invalid_type"]
    assert (
        failures["raw.customers|duplicate_in_batch"]
        == defects["customers_csv", "duplicate_in_file"]
    )
    assert quarantined["customers_csv"] == 7
    # Pedidos
    assert failures["raw.orders|type:items.quantity"] == defects["orders_json", "invalid_type"]
    assert failures["raw.orders|not_empty:items"] == defects["orders_json", "empty_collection"]
    assert failures["raw.orders|not_null:customer_id"] == defects["orders_json", "missing_required"]
    assert failures["raw.orders|range:items.quantity"] == defects["orders_json", "out_of_range"]
    assert quarantined["orders_json"] == 7
    # Productos (versión del día 1)
    assert (
        failures["raw.products|not_null:price"] + failures["raw.products|not_null:title"]
        == (defects["products_api", "missing_required"])
    )
    assert failures["raw.products|range:price"] == defects["products_api", "out_of_range"]
    assert quarantined["products_api"] == 4
    # Nada más falló: ninguna regla de error inesperada con fallos.
    unexpected = {
        k: v
        for k, v in failures.items()
        if v
        and k.split("|")[1]
        not in {
            "not_null:email",
            "format:email",
            "type:signup_date",
            "duplicate_in_batch",
            "type:items.quantity",
            "not_empty:items",
            "not_null:customer_id",
            "range:items.quantity",
            "not_null:price",
            "not_null:title",
            "range:price",
        }
    }
    assert unexpected == {}


def test_raw_counts_and_lineage(pipeline_engine: Engine, migrated_db: Engine) -> None:
    outcomes = ingest_all_files(pipeline_engine)
    statuses = {o.source_ref: o.status for o in outcomes}
    assert statuses["customers_2026-01-02_resend.csv"] == "skipped"  # mismo hash que el del día 2

    exp = MANIFEST["expectations"]
    # Un cliente en cuarentena el día 1 puede volver limpio en un delta posterior y cargarse.
    row_defects = {"missing_required", "invalid_type", "invalid_format"}
    quarantined_day1 = {
        d["key"]
        for d in MANIFEST["defects"]
        if d["source"] == "customers_csv" and d["kind"] in row_defects
    }
    later = {
        line.split(",", 1)[0]
        for name in ("customers_2026-01-02.csv", "customers_2026-01-03.csv")
        for line in (SAMPLES / "customers" / name).read_text(encoding="utf-8").splitlines()[1:]
    }
    never_loaded = quarantined_day1 - later
    assert scalar(migrated_db, "SELECT count(DISTINCT customer_id) FROM raw.customers") == (
        exp["customers_distinct"] - len(never_loaded)
    )
    # 5 pedidos reenviados idénticos se descartan por hash de registro.
    assert (
        scalar(migrated_db, "SELECT count(*) FROM raw.orders") == exp["order_records_total"] - 5 - 7
    )
    # 5 actualizaciones tardías: dos versiones del mismo pedido conviven en raw.
    assert (
        scalar(
            migrated_db,
            "SELECT count(*) FROM "
            "(SELECT order_id FROM raw.orders GROUP BY 1 HAVING count(*) = 2) x",
        )
        == 5
    )
    # Cambio de esquema del día 3: la columna nueva se conserva en _extra.
    assert (
        scalar(migrated_db, "SELECT count(*) FROM raw.customers WHERE _extra ? 'loyalty_tier'")
        == 35
    )
    # Trazabilidad: toda fila apunta a un lote exitoso con su archivo de origen.
    assert (
        scalar(
            migrated_db,
            "SELECT count(*) FROM raw.orders o JOIN ops.ingestion_registry r "
            "ON r.batch_id = o._batch_id "
            "WHERE r.status <> 'success' OR r.source_ref <> o._source_ref",
        )
        == 0
    )


def test_reingesting_everything_changes_nothing(
    pipeline_engine: Engine, migrated_db: Engine
) -> None:
    ingest_all_files(pipeline_engine)
    before = [scalar(migrated_db, sql) for sql in COUNTS]
    second = ingest_all_files(pipeline_engine)
    assert {o.status for o in second} == {"skipped"}
    after = [scalar(migrated_db, sql) for sql in COUNTS]
    assert before == after


def test_products_incremental_by_watermark(
    pipeline_engine: Engine, migrated_db: Engine, mock_api: TestClient
) -> None:
    start = MANIFEST["config"]["start"]
    set_clock(mock_api, start + DAY_END)
    day1 = run_products(pipeline_engine, mock_api)
    assert (day1.rows_loaded, day1.rows_quarantined) == (190, 4)
    wm1 = scalar(migrated_db, "SELECT watermark_value FROM ops.watermarks")

    again = run_products(pipeline_engine, mock_api)
    assert again.rows_loaded == 0  # sin cambios: solo la ventana de solapamiento

    set_clock(mock_api, "2026-01-02" + DAY_END)
    day2 = run_products(pipeline_engine, mock_api)
    assert day2.rows_loaded == MANIFEST["config"]["changed_products_per_day"]
    assert scalar(migrated_db, "SELECT watermark_value FROM ops.watermarks") > wm1

    set_clock(mock_api, None)
    day3 = run_products(pipeline_engine, mock_api)
    cfg = MANIFEST["config"]
    assert day3.rows_loaded == cfg["changed_products_per_day"] + cfg["n_new_products"]
    assert (
        scalar(migrated_db, "SELECT count(*) FROM raw.products")
        == MANIFEST["expectations"]["product_versions"] - 4
    )


# ------------------------------------------------------------------ fallos y recuperación
def test_failure_mid_load_leaves_nothing_and_retry_reuses_the_batch(
    pipeline_engine: Engine, migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = SAMPLES / "customers/customers_2026-01-01.csv"

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("caída simulada a mitad de la carga")

    monkeypatch.setattr(load_module, "_insert_quarantine", boom)
    with pytest.raises(RuntimeError), RunRecorder(pipeline_engine, "t", trigger="test") as run:
        ingest_customers_file(pipeline_engine, run, path)
    assert scalar(migrated_db, "SELECT count(*) FROM raw.customers") == 0  # atomicidad
    failed_batch = scalar(
        migrated_db, "SELECT batch_id FROM ops.ingestion_registry WHERE status='failed'"
    )
    assert scalar(migrated_db, "SELECT status FROM ops.pipeline_runs") == "failed"

    monkeypatch.undo()
    with RunRecorder(pipeline_engine, "t", trigger="test") as run:
        outcome = ingest_customers_file(pipeline_engine, run, path)
    assert outcome.status == "success"
    assert outcome.batch_id == failed_batch  # el reintento reutiliza el lote
    assert scalar(migrated_db, "SELECT count(*) FROM raw.customers") == 193
    assert scalar(migrated_db, "SELECT count(*) FROM ops.ingestion_registry") == 1


def test_watermark_does_not_move_when_the_load_fails(
    pipeline_engine: Engine,
    migrated_db: Engine,
    mock_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        load_module, "_insert_rows", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with pytest.raises(RuntimeError):
        run_products(pipeline_engine, mock_api)
    assert scalar(migrated_db, "SELECT count(*) FROM ops.watermarks") == 0


def test_concurrent_load_of_the_same_source_is_refused(
    pipeline_engine: Engine, migrated_db: Engine
) -> None:
    with migrated_db.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(hashtext('datapulse:customers_csv'))"))
        try:
            with (
                pytest.raises(ConcurrentIngestionError),
                RunRecorder(pipeline_engine, "t", trigger="test") as run,
            ):
                ingest_customers_file(
                    pipeline_engine, run, SAMPLES / "customers/customers_2026-01-02.csv"
                )
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(hashtext('datapulse:customers_csv'))"))
    assert scalar(migrated_db, "SELECT count(*) FROM raw.customers") == 0


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"customer_id,email\nC00001,a@example.com\n", "faltan columnas obligatorias"),
        (b"\xff\xfe\x00garbage", "CSV ilegible"),
    ],
)
def test_broken_files_are_rejected_once(
    pipeline_engine: Engine, migrated_db: Engine, tmp_path: Path, content: bytes, reason: str
) -> None:
    path = tmp_path / "customers_broken.csv"
    path.write_bytes(content)
    with RunRecorder(pipeline_engine, "t", trigger="test") as run:
        first = ingest_customers_file(pipeline_engine, run, path)
        second = ingest_customers_file(pipeline_engine, run, path)
    assert first.status == "rejected"
    assert reason in str(first.reason)
    assert second.status == "skipped"
    assert scalar(migrated_db, "SELECT status FROM ops.pipeline_runs") == "partial"
    assert scalar(migrated_db, "SELECT count(*) FROM raw.customers") == 0


def test_run_and_task_tracking(pipeline_engine: Engine, migrated_db: Engine) -> None:
    ingest_all_files(pipeline_engine)
    with migrated_db.connect() as conn:
        runs = conn.execute(
            text(
                "SELECT flow_name, status, rows_read, rows_loaded, rows_rejected "
                "FROM ops.pipeline_runs ORDER BY started_at"
            )
        ).all()
        tasks = conn.execute(
            text("SELECT task_name, status FROM ops.task_runs ORDER BY started_at")
        ).all()
    assert [(r.flow_name, r.status) for r in runs] == [
        ("test_customers", "partial"),
        ("test_orders", "partial"),
    ]
    assert runs[0].rows_loaded == 193 + 35 + 35
    assert runs[1].rows_rejected == 7
    assert len(tasks) == 7
    assert {t.status for t in tasks} == {"success"}


def test_pipeline_role_cannot_rewrite_raw(pipeline_engine: Engine) -> None:
    ingest_all_files(pipeline_engine)
    from sqlalchemy.exc import ProgrammingError  # noqa: PLC0415

    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        pipeline_engine.begin() as conn,
    ):
        conn.execute(text("UPDATE raw.customers SET email = 'x@example.com'"))


# ------------------------------------------------------------------ CLI
def _cli_env(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    url = engine.url
    monkeypatch.setenv("DATAPULSE_DB_HOST", str(url.host))
    monkeypatch.setenv("DATAPULSE_DB_PORT", str(url.port))
    monkeypatch.setenv("DATAPULSE_DB_NAME", str(url.database))
    monkeypatch.setenv("DATAPULSE_DB_USER", str(url.username))
    monkeypatch.setenv("DATAPULSE_DB_PASSWORD", str(url.password))
    monkeypatch.setenv("DATAPULSE_LOG_LEVEL", "WARNING")


def test_cli_ingests_files_and_reports_json(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datapulse.ingestion.__main__ import main  # noqa: PLC0415

    _cli_env(monkeypatch, migrated_db)
    files = [str(p) for p in sorted((SAMPLES / "orders").glob("*.json"))]
    assert main(["orders", *files]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert [o["status"] for o in summary] == ["success", "success", "success"]
    assert sum(o["rows_loaded"] for o in summary) == 448
    assert main(["customers", str(SAMPLES / "customers/customers_2026-01-02.csv")]) == 0


def test_cli_returns_1_when_the_source_is_down(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datapulse.ingestion.__main__ import main  # noqa: PLC0415

    _cli_env(monkeypatch, migrated_db)
    monkeypatch.setenv("DATAPULSE_INGEST_MAX_RETRIES", "0")
    assert main(["products", "--base-url", "http://127.0.0.1:9"]) == 1
    assert "SourceError" in capsys.readouterr().err
    assert scalar(migrated_db, "SELECT status FROM ops.pipeline_runs") == "failed"
