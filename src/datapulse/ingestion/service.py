"""Casos de uso de ingesta: archivo o API → validación → carga idempotente.

Los flujos de Prefect (fase 4) llaman a estas funciones; también se pueden usar
desde la CLI o desde pruebas, sin orquestador.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine

from datapulse.ingestion.hashing import bytes_hash, canonical_json, set_hash
from datapulse.ingestion.load import BatchOutcome, WatermarkUpdate, process_batch, read_watermark
from datapulse.ingestion.rest import ProductsClient
from datapulse.observability.runs import RunRecorder
from datapulse.validation.contracts import CUSTOMERS, PRODUCTS, flatten_product
from datapulse.validation.orders import parse_ts, validate_orders
from datapulse.validation.report import RuleResult, Severity, ValidationReport
from datapulse.validation.tabular import validate_tabular

logger = logging.getLogger(__name__)
PRODUCTS_SOURCE = "products_api"


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATAPULSE_INGEST_", env_file=".env", extra="ignore"
    )

    products_api_url: str = "http://127.0.0.1:8081"
    page_size: int = 50
    overlap_minutes: int = 60
    max_retries: int = 5
    timeout_s: float = 15.0
    landing_dir: Path = Path("data/landing")
    max_error_rate: float = 0.2


def _parse_failure(source: str, message: str) -> ValidationReport:
    report = ValidationReport(source=source, rows_read=0)
    report.rejected_reason = message
    report.results.append(
        RuleResult(
            "parse:file", Severity.ERROR, 0, 0, passed=False, details={"error": message[:500]}
        )
    )
    return report


def _record(recorder: RunRecorder, outcome: BatchOutcome) -> BatchOutcome:
    recorder.add(
        read=outcome.rows_read,
        loaded=outcome.rows_loaded,
        rejected=outcome.rows_read if outcome.status == "rejected" else outcome.rows_quarantined,
        partial=outcome.partial,
    )
    return outcome


def ingest_customers_file(engine: Engine, recorder: RunRecorder, path: Path) -> BatchOutcome:
    with recorder.task(f"ingest:{path.name}") as task:
        data = path.read_bytes()
        try:
            data.decode("utf-8")  # estricto: Polars aceptaría bytes inválidos sin avisar
            frame = pl.read_csv(io.BytesIO(data), infer_schema=False, encoding="utf8")
        except (pl.exceptions.PolarsError, UnicodeDecodeError) as exc:
            report = _parse_failure(CUSTOMERS.source, f"CSV ilegible: {exc}")
        else:
            report = validate_tabular(CUSTOMERS, frame)
        outcome = process_batch(engine, recorder.run_id, path.name, bytes_hash(data), report)
        task.rows_in, task.rows_out = outcome.rows_read, outcome.rows_loaded
    return _record(recorder, outcome)


def ingest_orders_file(engine: Engine, recorder: RunRecorder, path: Path) -> BatchOutcome:
    with recorder.task(f"ingest:{path.name}") as task:
        data = path.read_bytes()
        try:
            document = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            report = _parse_failure("orders_json", f"JSON ilegible: {exc}")
        else:
            report = validate_orders(document)
        outcome = process_batch(engine, recorder.run_id, path.name, bytes_hash(data), report)
        task.rows_in, task.rows_out = outcome.rows_read, outcome.rows_loaded
    return _record(recorder, outcome)


def _updated_at(record: dict[str, Any]) -> Any:
    meta = record.get("meta")
    return parse_ts(meta.get("updatedAt")) if isinstance(meta, dict) else None


def ingest_products(
    engine: Engine,
    recorder: RunRecorder,
    client: ProductsClient,
    *,
    base_url: str,
    overlap: timedelta = timedelta(hours=1),
) -> BatchOutcome:
    """Carga incremental: solo productos con ``updatedAt`` posterior a la marca de agua.

    DummyJSON no filtra por fecha en el servidor, así que se lee el catálogo y se
    filtra en el cliente. La ventana de solapamiento (``overlap``) recoge cambios
    que llegaron tarde; los ya cargados se descartan por hash de registro.
    """
    with recorder.task("ingest:products_api") as task:
        watermark = read_watermark(engine, PRODUCTS_SOURCE)
        since = watermark - overlap if watermark is not None else None
        fetched = client.fetch_all()
        selected = [
            r
            for r in fetched.records
            if since is None or (ts := _updated_at(r)) is None or ts > since
        ]
        source_ref = (
            f"{base_url.rstrip('/')}/products pages={fetched.stats.pages} "
            f"since={since.isoformat() if since else 'inicio'}"
        )
        task.rows_in = len(fetched.records)
        if not selected:
            task.rows_out = 0
            logger.info("products_no_changes", extra={"since": str(since)})
            return _record(recorder, BatchOutcome(PRODUCTS_SOURCE, source_ref, "empty"))

        known, extras = zip(*(flatten_product(r) for r in selected), strict=True)
        frame = pl.DataFrame(list(known), schema=dict.fromkeys(PRODUCTS.names, pl.String))
        report = validate_tabular(PRODUCTS, frame, extras=list(extras))
        if fetched.stats.total_changed:
            report.results.append(
                RuleResult(
                    "source:total_changed_during_pagination",
                    Severity.WARN,
                    len(selected),
                    0,
                    passed=False,
                )
            )
        timestamps = [ts for r in selected if (ts := _updated_at(r)) is not None]
        new_wm = (
            WatermarkUpdate(
                max(timestamps),
                cursor={
                    "pages": fetched.stats.pages,
                    "requests": fetched.stats.requests,
                    "retries": fetched.stats.retries,
                    "selected": len(selected),
                },
            )
            if timestamps
            else None
        )
        content = set_hash(bytes_hash(canonical_json(r).encode("utf-8")) for r in selected)
        outcome = process_batch(
            engine, recorder.run_id, source_ref, content, report, watermark=new_wm
        )
        task.rows_out = outcome.rows_loaded
    return _record(recorder, outcome)
