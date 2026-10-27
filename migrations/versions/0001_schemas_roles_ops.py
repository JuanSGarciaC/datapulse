"""Esquemas por capa, roles de grupo con mínimo privilegio y tablas operativas (ops).

Revision ID: 0001
Revises:
Create Date: 2026-10-27

Notas:
* Los roles son objetos del *clúster*, no de la base de datos: se crean con
  ``IF NOT EXISTS`` y el downgrade NO los elimina (pueden estar en uso por otra
  base del mismo clúster, p. ej. la de pruebas). Ver ADR-0005.
* ``raw`` es append-only por diseño: ``dp_pipeline`` solo recibe SELECT/INSERT.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GROUP_ROLES = ("dp_pipeline", "dp_transform", "dp_reader")
SCHEMAS = ("raw", "ops", "staging", "marts")


def _ts(name: str, *, nullable: bool = False, default_now: bool = True) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def upgrade() -> None:
    for role in GROUP_ROLES:
        op.execute(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {role} NOLOGIN; END IF; END $$;"
        )
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for schema in SCHEMAS:
        op.execute(f"COMMENT ON SCHEMA {schema} IS 'DataPulse capa {schema}'")

    # ------------------------------------------------------------------ ops.*
    op.create_table(
        "pipeline_runs",
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("flow_name", sa.String(100), nullable=False),
        sa.Column("source_name", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False, server_default=sa.text("'manual'")),
        _ts("started_at"),
        _ts("finished_at", nullable=True, default_now=False),
        sa.Column("rows_read", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("rows_loaded", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("rows_rejected", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("error_type", sa.String(200)),
        sa.Column("error_message", sa.Text),
        sa.Column("orchestrator_run_id", pg.UUID(as_uuid=True)),
        sa.Column("params", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("run_id", name="pk_pipeline_runs"),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'partial')",
            name="ck_pipeline_runs_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'manual', 'api', 'test')", name="ck_pipeline_runs_trigger"
        ),
        sa.CheckConstraint(
            "rows_read >= 0 AND rows_loaded >= 0 AND rows_rejected >= 0",
            name="ck_pipeline_runs_rows",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_pipeline_runs_finished_after",
        ),
        schema="ops",
    )
    op.create_index(
        "ix_pipeline_runs_flow_started",
        "pipeline_runs",
        ["flow_name", sa.text("started_at DESC")],
        schema="ops",
    )
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"], schema="ops")

    op.create_table(
        "task_runs",
        sa.Column("task_run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("task_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False, server_default=sa.text("1")),
        _ts("started_at"),
        _ts("finished_at", nullable=True, default_now=False),
        sa.Column("rows_in", sa.BigInteger),
        sa.Column("rows_out", sa.BigInteger),
        sa.Column("error_message", sa.Text),
        sa.PrimaryKeyConstraint("task_run_id", name="pk_task_runs"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ops.pipeline_runs.run_id"],
            name="fk_task_runs_run_id_pipeline_runs",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'skipped')", name="ck_task_runs_status"
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_task_runs_attempt"),
        schema="ops",
    )
    op.create_index("ix_task_runs_run_id", "task_runs", ["run_id"], schema="ops")

    op.create_table(
        "ingestion_registry",
        sa.Column("batch_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("source_ref", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("row_count", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("rejected_count", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("batch_id", name="pk_ingestion_registry"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ops.pipeline_runs.run_id"],
            name="fk_ingestion_registry_run_id_pipeline_runs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "source_name",
            "content_sha256",
            name="uq_ingestion_registry_source_name_content_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'success', 'failed', 'rejected')",
            name="ck_ingestion_registry_status",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_ingestion_registry_sha256_format"
        ),
        schema="ops",
    )

    op.create_table(
        "watermarks",
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("watermark_value", sa.DateTime(timezone=True)),
        sa.Column("cursor", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("run_id", pg.UUID(as_uuid=True)),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("source_name", name="pk_watermarks"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ops.pipeline_runs.run_id"],
            name="fk_watermarks_run_id_pipeline_runs",
            ondelete="SET NULL",
        ),
        schema="ops",
    )

    op.create_table(
        "quality_results",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", pg.UUID(as_uuid=True)),
        sa.Column("layer", sa.String(20), nullable=False),
        sa.Column("table_name", sa.String(200), nullable=False),
        sa.Column("rule_name", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("rows_evaluated", sa.BigInteger, nullable=False),
        sa.Column("rows_failed", sa.BigInteger, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("details", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("checked_at"),
        sa.PrimaryKeyConstraint("id", name="pk_quality_results"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ops.pipeline_runs.run_id"],
            name="fk_quality_results_run_id_pipeline_runs",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("severity IN ('error', 'warn')", name="ck_quality_results_severity"),
        sa.CheckConstraint(
            "layer IN ('pre_load', 'post_transform')", name="ck_quality_results_layer"
        ),
        sa.CheckConstraint(
            "rows_failed >= 0 AND rows_failed <= rows_evaluated", name="ck_quality_results_rows"
        ),
        schema="ops",
    )
    op.create_index("ix_quality_results_run_id", "quality_results", ["run_id"], schema="ops")
    op.create_index(
        "ix_quality_results_table_checked",
        "quality_results",
        ["table_name", "checked_at"],
        schema="ops",
    )

    # ------------------------------------------------------------- permisos
    op.execute("REVOKE ALL ON SCHEMA raw, ops, staging, marts FROM PUBLIC")

    # Pipeline: escribe en raw (solo INSERT, append-only) y registra su actividad en ops.
    op.execute("GRANT USAGE ON SCHEMA raw, ops TO dp_pipeline")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ops TO dp_pipeline")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ops TO dp_pipeline")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT SELECT, INSERT ON TABLES TO dp_pipeline"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT USAGE, SELECT ON SEQUENCES TO dp_pipeline"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA ops "
        "GRANT SELECT, INSERT, UPDATE ON TABLES TO dp_pipeline"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT USAGE, SELECT ON SEQUENCES TO dp_pipeline"
    )

    # Transformación (dbt): lee raw, es dueño de staging y marts.
    op.execute("GRANT USAGE ON SCHEMA raw TO dp_transform")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT SELECT ON TABLES TO dp_transform")
    op.execute("GRANT USAGE, CREATE ON SCHEMA staging, marts TO dp_transform")

    # Lectura (API): solo SELECT sobre marts y ops. Nunca ve raw ni staging.
    op.execute("GRANT USAGE ON SCHEMA marts, ops TO dp_reader")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA ops TO dp_reader")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO dp_reader")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE dp_transform IN SCHEMA marts "
        "GRANT SELECT ON TABLES TO dp_reader"
    )


def downgrade() -> None:
    # Destructivo a propósito: el downgrade a base deja la base como estaba antes de 0001.
    # Los roles de grupo se conservan (objetos de clúster, ver docstring).
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
