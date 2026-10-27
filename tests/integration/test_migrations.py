"""Migraciones contra PostgreSQL real: estructura, reversibilidad, restricciones y deriva."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine, insert, inspect, text
from sqlalchemy.exc import IntegrityError

from datapulse.db.schema import metadata, pipeline_runs
from tests.conftest import alembic_config

pytestmark = pytest.mark.integration

EXPECTED_OPS_TABLES = {
    "pipeline_runs",
    "task_runs",
    "ingestion_registry",
    "watermarks",
    "quality_results",
}


def test_upgrade_creates_layer_schemas_and_ops_tables(migrated_db: Engine) -> None:
    insp = inspect(migrated_db)
    assert {"raw", "ops", "staging", "marts"} <= set(insp.get_schema_names())
    assert set(insp.get_table_names(schema="ops")) == EXPECTED_OPS_TABLES


def test_no_schema_drift_between_code_and_migrations(migrated_db: Engine) -> None:
    with migrated_db.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "include_name": lambda name, type_, _p: type_ != "schema" or name in {"ops", "raw"},
                "compare_type": True,
            },
        )
        diff = compare_metadata(ctx, metadata)
    assert diff == [], f"Deriva entre schema.py y migraciones: {diff}"


def test_downgrade_and_upgrade_roundtrip(migrated_db: Engine) -> None:
    cfg = alembic_config(migrated_db)
    command.downgrade(cfg, "base")
    assert "ops" not in inspect(migrated_db).get_schema_names()
    command.upgrade(cfg, "head")
    # Re-ejecutar upgrade en head no debe hacer nada (idempotencia de la herramienta).
    command.upgrade(cfg, "head")
    assert set(inspect(migrated_db).get_table_names(schema="ops")) == EXPECTED_OPS_TABLES


def _insert_run(conn: Connection, **overrides: object) -> uuid.UUID:
    run_id = uuid.uuid4()
    values = {"run_id": run_id, "flow_name": "f", "status": "running"} | overrides
    conn.execute(insert(pipeline_runs).values(**values))
    return run_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "exploded"},
        {"trigger": "cron-ish"},
        {"rows_read": -1},
        {"started_at": "2026-01-02T00:00:00Z", "finished_at": "2026-01-01T00:00:00Z"},
    ],
)
def test_pipeline_runs_constraints_reject_invalid_rows(
    migrated_db: Engine, overrides: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError), migrated_db.begin() as conn:
        _insert_run(conn, **overrides)


def test_ingestion_registry_enforces_one_batch_per_content(migrated_db: Engine) -> None:
    sha = "a" * 64
    insert = text(
        "INSERT INTO ops.ingestion_registry "
        "(batch_id, source_name, source_ref, content_sha256, status) "
        "VALUES (:b, 'customers_csv', :ref, :sha, 'success')"
    )
    with migrated_db.begin() as conn:
        conn.execute(insert, {"b": uuid.uuid4(), "ref": "customers_2026-01-01.csv", "sha": sha})
    # Mismo contenido con otro nombre de archivo: sigue siendo el mismo lote.
    with pytest.raises(IntegrityError), migrated_db.begin() as conn:
        conn.execute(insert, {"b": uuid.uuid4(), "ref": "copia.csv", "sha": sha})


def test_ingestion_registry_rejects_malformed_hash(migrated_db: Engine) -> None:
    with pytest.raises(IntegrityError), migrated_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops.ingestion_registry "
                "(batch_id, source_name, source_ref, content_sha256, status) "
                "VALUES (:b, 's', 'r', 'NOT-A-HASH', 'success')"
            ),
            {"b": uuid.uuid4()},
        )


def test_deleting_a_run_cascades_to_task_runs(migrated_db: Engine) -> None:
    with migrated_db.begin() as conn:
        run_id = _insert_run(conn)
        conn.execute(
            text(
                "INSERT INTO ops.task_runs (task_run_id, run_id, task_name, status) "
                "VALUES (:t, :r, 'extract', 'success')"
            ),
            {"t": uuid.uuid4(), "r": run_id},
        )
        conn.execute(text("DELETE FROM ops.pipeline_runs WHERE run_id = :r"), {"r": run_id})
        remaining = conn.execute(text("SELECT count(*) FROM ops.task_runs")).scalar_one()
    assert remaining == 0
