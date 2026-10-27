"""CLI de ingesta (sin orquestador).

    python -m datapulse.ingestion customers data/samples/customers/*.csv
    python -m datapulse.ingestion orders data/samples/orders/*.json
    python -m datapulse.ingestion products [--base-url URL]

Conecta con ``DATAPULSE_DB_*`` (en producción: el usuario ``datapulse_pipeline``).
Imprime un resumen JSON por lote. Código de salida: 0 éxito, 1 fallo.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx2 as httpx
from sqlalchemy import Engine

from datapulse.config import get_settings
from datapulse.db.engine import build_engine
from datapulse.ingestion.load import BatchOutcome
from datapulse.ingestion.rest import ProductsClient, RetryPolicy
from datapulse.ingestion.service import (
    IngestionSettings,
    ingest_customers_file,
    ingest_orders_file,
    ingest_products,
)
from datapulse.observability.logging import configure_logging
from datapulse.observability.runs import RunRecorder

FileIngestor = Callable[[Engine, RunRecorder, Path], BatchOutcome]


def run_files(
    engine: Engine, flow: str, source: str, ingest: FileIngestor, paths: list[Path]
) -> list[BatchOutcome]:
    outcomes = []
    with RunRecorder(
        engine, flow, source_name=source, params={"files": [p.name for p in paths]}
    ) as run:
        # Orden por nombre = orden cronológico de los archivos de la fuente.
        for path in sorted(paths, key=lambda p: p.name):
            outcomes.append(ingest(engine, run, path))
    return outcomes


def run_products(engine: Engine, cfg: IngestionSettings, base_url: str) -> list[BatchOutcome]:
    policy = RetryPolicy(max_retries=cfg.max_retries)
    with (
        httpx.Client(base_url=base_url, timeout=cfg.timeout_s) as http,
        RunRecorder(
            engine, "ingest_products", source_name="products_api", params={"base_url": base_url}
        ) as run,
    ):
        client = ProductsClient(http, page_size=cfg.page_size, policy=policy)
        outcome = ingest_products(
            engine, run, client, base_url=base_url, overlap=timedelta(minutes=cfg.overlap_minutes)
        )
    return [outcome]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingesta de DataPulse")
    sub = parser.add_subparsers(dest="source", required=True)
    for name in ("customers", "orders"):
        sp = sub.add_parser(name)
        sp.add_argument("paths", nargs="+", type=Path)
    sp = sub.add_parser("products")
    sp.add_argument("--base-url", default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    cfg = IngestionSettings()
    engine = build_engine(settings.db)
    try:
        if args.source == "customers":
            outcomes = run_files(
                engine, "ingest_customers", "customers_csv", ingest_customers_file, args.paths
            )
        elif args.source == "orders":
            outcomes = run_files(
                engine, "ingest_orders", "orders_json", ingest_orders_file, args.paths
            )
        else:
            outcomes = run_products(engine, cfg, args.base_url or cfg.products_api_url)
    except Exception as exc:
        print(
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    finally:
        engine.dispose()
    print(json.dumps([o.as_dict() for o in outcomes], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
