"""CLI de orquestación.

    python -m datapulse.flows seed-landing            # copia data/samples → data/landing
    python -m datapulse.flows run daily               # ejecuta un flujo ahora
    python -m datapulse.flows run products --base-url https://dummyjson.com
    python -m datapulse.flows serve                   # worker: despliegues programados

``serve`` necesita ``PREFECT_API_URL`` apuntando al servidor de Prefect (en Docker
Compose: ``http://prefect-server:4200/api``).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from prefect import serve
from pydantic_settings import BaseSettings, SettingsConfigDict

from datapulse.config import get_settings
from datapulse.flows.pipeline import (
    daily_pipeline,
    ingest_files_flow,
    ingest_products_flow,
    seed_landing,
    transform_flow,
)
from datapulse.observability.logging import configure_logging


class ScheduleSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATAPULSE_FLOW_", env_file=".env", extra="ignore")

    daily_cron: str = "0 6 * * *"  # 06:00 UTC
    products_interval_minutes: int = 60


def deployments() -> list[Any]:
    cfg = ScheduleSettings()
    return [
        daily_pipeline.to_deployment(
            name="daily",
            cron=cfg.daily_cron,
            parameters={"trigger": "schedule"},
            description="Clientes, pedidos, productos y transformación.",
            tags=["datapulse"],
        ),
        ingest_products_flow.to_deployment(
            name="products-incremental",
            interval=timedelta(minutes=cfg.products_interval_minutes),
            parameters={"trigger": "schedule"},
            description="Carga incremental de productos por marca de agua.",
            tags=["datapulse"],
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orquestación de DataPulse")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed-landing")
    seed.add_argument("--samples", type=Path, default=Path("data/samples"))
    seed.add_argument("--landing", type=Path, default=Path("data/landing"))
    run = sub.add_parser("run")
    run.add_argument("flow", choices=["daily", "customers", "orders", "products", "transform"])
    run.add_argument("--full-refresh", action="store_true")
    run.add_argument("--landing-dir", default=None)
    run.add_argument("--base-url", default=None)
    run.add_argument("--no-transform", action="store_true")
    sub.add_parser("serve")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    if args.command == "seed-landing":
        copied = seed_landing(args.samples, args.landing)
        print(json.dumps([str(p) for p in copied], indent=2))
        return 0
    if args.command == "serve":  # pragma: no cover - proceso de larga duración
        serve(*deployments())
        return 0

    state: Any
    if args.flow == "daily":
        state = daily_pipeline(
            landing_dir=args.landing_dir,
            base_url=args.base_url,
            transform=not args.no_transform,
            return_state=True,
        )
    elif args.flow == "transform":
        state = transform_flow(full_refresh=args.full_refresh, return_state=True)
    elif args.flow == "products":
        state = ingest_products_flow(base_url=args.base_url, return_state=True)
    else:
        state = ingest_files_flow(args.flow, landing_dir=args.landing_dir, return_state=True)
    print(json.dumps({"flow": args.flow, "state": state.type.value, "message": state.message}))
    return 1 if state.is_failed() else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
