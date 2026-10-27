"""Carga idempotente de un lote validado en ``raw`` (una transacción por lote).

Garantías:

* **Lote ya procesado** (mismo ``source`` + sha256 en estado success/rejected) → se omite.
* **Registro ya cargado** (mismo ``_record_hash``) → ``ON CONFLICT DO NOTHING``.
* **Atomicidad:** filas, cuarentena, resultados de calidad, registro del lote y
  marca de agua se confirman juntos o no se confirma nada.
* **Concurrencia:** un bloqueo consultivo por fuente impide dos cargas simultáneas.
* **Recuperación:** si falla, el lote queda ``failed`` y el siguiente intento lo
  reutiliza (mismo ``batch_id``) sin duplicar nada.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import Connection, Engine, Table, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from datapulse.db.schema import (
    ingestion_registry,
    quality_results,
    quarantine,
    raw_customers,
    raw_orders,
    raw_products,
    watermarks,
)
from datapulse.ingestion.hashing import canonical_json, record_hash
from datapulse.validation.report import ValidationReport

logger = logging.getLogger(__name__)

TABLES: Final[dict[str, Table]] = {
    "customers_csv": raw_customers,
    "orders_json": raw_orders,
    "products_api": raw_products,
}
CHUNK: Final = 1000

Status = Literal["success", "rejected", "skipped", "empty"]


class ConcurrentIngestionError(RuntimeError):
    """Otra ejecución está cargando la misma fuente."""


@dataclass(frozen=True, slots=True)
class WatermarkUpdate:
    value: datetime
    cursor: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BatchOutcome:
    source: str
    source_ref: str
    status: Status
    batch_id: uuid.UUID | None = None
    rows_read: int = 0
    rows_loaded: int = 0
    rows_already_loaded: int = 0
    rows_quarantined: int = 0
    duplicates_in_batch: int = 0
    reason: str | None = None

    @property
    def partial(self) -> bool:
        return self.status == "rejected" or self.rows_quarantined > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_ref": self.source_ref,
            "status": self.status,
            "batch_id": str(self.batch_id) if self.batch_id else None,
            "rows_read": self.rows_read,
            "rows_loaded": self.rows_loaded,
            "rows_already_loaded": self.rows_already_loaded,
            "rows_quarantined": self.rows_quarantined,
            "duplicates_in_batch": self.duplicates_in_batch,
            "reason": self.reason,
        }


def _chunks(rows: Sequence[dict[str, Any]]) -> list[Sequence[dict[str, Any]]]:
    return [rows[i : i + CHUNK] for i in range(0, len(rows), CHUNK)]


def _insert_rows(
    conn: Connection,
    table: Table,
    records: list[dict[str, Any]],
    batch_id: uuid.UUID,
    source_ref: str,
) -> int:
    rows = [
        {
            **{k: v for k, v in record.items() if not k.startswith("_")},
            "_batch_id": batch_id,
            "_source_ref": source_ref,
            "_row_number": record.get("_row_number"),
            "_record_hash": record_hash(record),
            "_extra": record.get("_extra", {}),
        }
        for record in records
    ]
    inserted = 0
    for chunk in _chunks(rows):
        stmt = (
            pg_insert(table)
            .values(list(chunk))
            .on_conflict_do_nothing(index_elements=["_record_hash"])
            .returning(table.c._raw_id)
        )
        inserted += len(conn.execute(stmt).all())
    return inserted


def _insert_quarantine(
    conn: Connection, report: ValidationReport, batch_id: uuid.UUID, source_ref: str
) -> None:
    rows = [
        {
            "_batch_id": batch_id,
            "source_name": report.source,
            "_source_ref": source_ref,
            "_row_number": q.row_number,
            "record_key": q.record_key,
            "_record_hash": record_hash({"payload": canonical_json(q.payload)}),
            "payload": q.payload,
            "errors": [e.as_dict() for e in q.errors],
        }
        for q in report.quarantined
    ]
    for chunk in _chunks(rows):
        conn.execute(
            pg_insert(quarantine)
            .values(list(chunk))
            .on_conflict_do_nothing(index_elements=["source_name", "_record_hash"])
        )


def _insert_quality(
    conn: Connection, run_id: uuid.UUID, batch_id: uuid.UUID, table: Table, report: ValidationReport
) -> None:
    if not report.results:
        return
    conn.execute(
        quality_results.insert(),
        [
            {
                "run_id": run_id,
                "batch_id": batch_id,
                "layer": "pre_load",
                "table_name": f"{table.schema}.{table.name}",
                "rule_name": r.rule,
                "severity": str(r.severity),
                "rows_evaluated": r.rows_evaluated,
                "rows_failed": r.rows_failed,
                "passed": bool(r.passed),
                "details": {"sample_keys": r.sample_keys, **r.details},
            }
            for r in report.results
        ],
    )


def _upsert_watermark(
    conn: Connection, source: str, run_id: uuid.UUID, wm: WatermarkUpdate
) -> None:
    stmt = pg_insert(watermarks).values(
        source_name=source, watermark_value=wm.value, cursor=wm.cursor, run_id=run_id
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["source_name"],
            set_={
                # Nunca retrocede, aunque llegue un lote más antiguo.
                "watermark_value": func.greatest(
                    watermarks.c.watermark_value, stmt.excluded.watermark_value
                ),
                "cursor": stmt.excluded.cursor,
                "run_id": stmt.excluded.run_id,
                "updated_at": func.now(),
            },
        )
    )


def _mark_failed(engine: Engine, run_id: uuid.UUID, source: str, source_ref: str, sha: str) -> None:
    stmt = pg_insert(ingestion_registry).values(
        batch_id=uuid.uuid4(),
        source_name=source,
        source_ref=source_ref,
        content_sha256=sha,
        run_id=run_id,
        status="failed",
    )
    with engine.begin() as conn:
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["source_name", "content_sha256"],
                set_={"status": "failed", "run_id": run_id, "updated_at": func.now()},
            )
        )


def process_batch(
    engine: Engine,
    run_id: uuid.UUID,
    source_ref: str,
    content_sha256: str,
    report: ValidationReport,
    *,
    watermark: WatermarkUpdate | None = None,
) -> BatchOutcome:
    source = report.source
    table = TABLES[source]
    outcome = BatchOutcome(
        source=source,
        source_ref=source_ref,
        status="success",
        rows_read=report.rows_read,
        duplicates_in_batch=report.duplicates_dropped,
    )
    try:
        with engine.begin() as conn:
            locked = conn.execute(
                select(func.pg_try_advisory_xact_lock(func.hashtext(f"datapulse:{source}")))
            ).scalar_one()
            if not locked:
                raise ConcurrentIngestionError(f"otra ejecución está cargando {source}")

            existing = conn.execute(
                select(ingestion_registry.c.batch_id, ingestion_registry.c.status)
                .where(
                    ingestion_registry.c.source_name == source,
                    ingestion_registry.c.content_sha256 == content_sha256,
                )
                .with_for_update()
            ).first()
            if existing is not None and existing.status in {"success", "rejected"}:
                outcome.status = "skipped"
                outcome.batch_id = existing.batch_id
                outcome.reason = (
                    f"contenido ya procesado (lote {existing.batch_id}, {existing.status})"
                )
                return outcome

            batch_id = existing.batch_id if existing is not None else uuid.uuid4()
            outcome.batch_id = batch_id
            if existing is None:
                conn.execute(
                    ingestion_registry.insert().values(
                        batch_id=batch_id,
                        source_name=source,
                        source_ref=source_ref,
                        content_sha256=content_sha256,
                        run_id=run_id,
                        status="processing",
                    )
                )

            _insert_quality(conn, run_id, batch_id, table, report)
            if report.rejected:
                outcome.status = "rejected"
                outcome.reason = report.rejected_reason
                final_status, rejected_count = "rejected", report.rows_read
            else:
                outcome.rows_loaded = _insert_rows(conn, table, report.valid, batch_id, source_ref)
                outcome.rows_already_loaded = len(report.valid) - outcome.rows_loaded
                outcome.rows_quarantined = len(report.quarantined)
                _insert_quarantine(conn, report, batch_id, source_ref)
                if watermark is not None:
                    _upsert_watermark(conn, source, run_id, watermark)
                final_status, rejected_count = "success", len(report.quarantined)

            conn.execute(
                update(ingestion_registry)
                .where(ingestion_registry.c.batch_id == batch_id)
                .values(
                    status=final_status,
                    run_id=run_id,
                    source_ref=source_ref,
                    row_count=report.rows_read,
                    rejected_count=rejected_count,
                    updated_at=func.now(),
                )
            )
    except ConcurrentIngestionError:
        raise
    except Exception:
        logger.exception("batch_failed", extra={"source": source, "source_ref": source_ref})
        _mark_failed(engine, run_id, source, source_ref, content_sha256)
        raise
    logger.info("batch_processed", extra=outcome.as_dict())
    return outcome


def read_watermark(engine: Engine, source: str) -> datetime | None:
    with engine.connect() as conn:
        value = conn.execute(
            select(watermarks.c.watermark_value).where(watermarks.c.source_name == source)
        ).scalar_one_or_none()
    return value
