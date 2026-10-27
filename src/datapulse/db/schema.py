"""Definición (SQLAlchemy Core) de las tablas que gestiona Alembic.

Esta metadata es la fuente de verdad del código Python. Las migraciones se
escriben a mano y una prueba de integración verifica que no hay deriva entre
ambas (``tests/integration/test_migrations.py::test_no_schema_drift``).

Los estados se modelan con CHECK constraints en lugar de tipos ENUM de
PostgreSQL: añadir un estado nuevo es un ALTER sencillo y reversible (ADR-0003).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

OPS: Final = "ops"
RAW: Final = "raw"

# Convención de nombres explícita: migraciones deterministas y legibles.
NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

RUN_STATUSES: Final = ("running", "success", "failed", "partial")
TASK_STATUSES: Final = ("running", "success", "failed", "skipped")
BATCH_STATUSES: Final = ("processing", "success", "failed", "rejected")
SEVERITIES: Final = ("error", "warn")
QUALITY_LAYERS: Final = ("pre_load", "post_transform")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("flow_name", String(100), nullable=False),
    Column("source_name", String(100)),
    Column("status", String(20), nullable=False),
    Column("trigger", String(20), nullable=False, server_default=text("'manual'")),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("rows_read", BigInteger, nullable=False, server_default=text("0")),
    Column("rows_loaded", BigInteger, nullable=False, server_default=text("0")),
    Column("rows_rejected", BigInteger, nullable=False, server_default=text("0")),
    Column("error_type", String(200)),
    Column("error_message", Text),
    Column("orchestrator_run_id", UUID(as_uuid=True)),
    Column("params", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    CheckConstraint(_in("status", RUN_STATUSES), name="status"),
    CheckConstraint(_in("trigger", ("schedule", "manual", "api", "test")), name="trigger"),
    CheckConstraint("rows_read >= 0 AND rows_loaded >= 0 AND rows_rejected >= 0", name="rows"),
    CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="finished_after"),
    schema=OPS,
)
Index("ix_pipeline_runs_flow_started", pipeline_runs.c.flow_name, pipeline_runs.c.started_at.desc())
Index("ix_pipeline_runs_status", pipeline_runs.c.status)

task_runs = Table(
    "task_runs",
    metadata,
    Column("task_run_id", UUID(as_uuid=True), primary_key=True),
    Column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey(f"{OPS}.pipeline_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("task_name", String(100), nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempt", Integer, nullable=False, server_default=text("1")),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("rows_in", BigInteger),
    Column("rows_out", BigInteger),
    Column("error_message", Text),
    CheckConstraint(_in("status", TASK_STATUSES), name="status"),
    CheckConstraint("attempt >= 1", name="attempt"),
    schema=OPS,
)
Index("ix_task_runs_run_id", task_runs.c.run_id)

ingestion_registry = Table(
    "ingestion_registry",
    metadata,
    Column("batch_id", UUID(as_uuid=True), primary_key=True),
    Column("source_name", String(100), nullable=False),
    Column("source_ref", Text, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey(f"{OPS}.pipeline_runs.run_id", ondelete="SET NULL"),
    ),
    Column("status", String(20), nullable=False),
    Column("row_count", BigInteger, nullable=False, server_default=text("0")),
    Column("rejected_count", BigInteger, nullable=False, server_default=text("0")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Idempotencia de archivos: el mismo contenido de la misma fuente es un único lote.
    UniqueConstraint("source_name", "content_sha256"),
    CheckConstraint(_in("status", BATCH_STATUSES), name="status"),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
    schema=OPS,
)

watermarks = Table(
    "watermarks",
    metadata,
    Column("source_name", String(100), primary_key=True),
    Column("watermark_value", DateTime(timezone=True)),
    Column("cursor", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey(f"{OPS}.pipeline_runs.run_id", ondelete="SET NULL"),
    ),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema=OPS,
)

quality_results = Table(
    "quality_results",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True),
    Column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey(f"{OPS}.pipeline_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("batch_id", UUID(as_uuid=True)),
    Column("layer", String(20), nullable=False),
    Column("table_name", String(200), nullable=False),
    Column("rule_name", String(200), nullable=False),
    Column("severity", String(10), nullable=False),
    Column("rows_evaluated", BigInteger, nullable=False),
    Column("rows_failed", BigInteger, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("checked_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(_in("severity", SEVERITIES), name="severity"),
    CheckConstraint(_in("layer", QUALITY_LAYERS), name="layer"),
    CheckConstraint("rows_failed >= 0 AND rows_failed <= rows_evaluated", name="rows"),
    schema=OPS,
)
Index("ix_quality_results_run_id", quality_results.c.run_id)
Index(
    "ix_quality_results_table_checked", quality_results.c.table_name, quality_results.c.checked_at
)
