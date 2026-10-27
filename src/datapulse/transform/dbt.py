"""Ejecución de dbt desde Python y registro de sus pruebas en ``ops.quality_results``.

Se usa ``dbtRunner`` (API programática oficial de dbt-core) en lugar de un
subproceso: se obtienen los resultados estructurados de cada modelo y prueba sin
analizar texto de la consola.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dbt.cli.main import dbtRunner
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine

from datapulse.db.schema import quality_results

logger = logging.getLogger(__name__)

FAILED_STATUSES = frozenset({"error", "fail"})


class DbtSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATAPULSE_DBT_", env_file=".env", extra="ignore")

    project_dir: Path = Path("dbt")
    target_dir: Path | None = None  # por defecto <project_dir>/target
    threads: int = 4


class TransformError(RuntimeError):
    """dbt terminó con modelos o pruebas en error."""


@dataclass(slots=True)
class NodeOutcome:
    unique_id: str
    resource_type: str
    name: str
    status: str
    failures: int | None
    severity: str | None
    attached_to: str | None
    message: str | None


@dataclass(slots=True)
class TransformOutcome:
    success: bool
    nodes: list[NodeOutcome] = field(default_factory=list)

    def count(self, resource_type: str, status: str | None = None) -> int:
        return sum(
            1
            for n in self.nodes
            if n.resource_type == resource_type and (status is None or n.status == status)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "models": self.count("model"),
            "tests": self.count("test"),
            "tests_warn": self.count("test", "warn"),
            "tests_failed": self.count("test", "fail") + self.count("test", "error"),
            "models_failed": self.count("model", "error"),
        }


def _qualified(unique_id: str) -> str:
    """``model.datapulse.stg_orders`` → ``staging.stg_orders``.

    ``source.datapulse.raw.orders`` → ``raw.orders``.
    """
    parts = unique_id.split(".")
    if parts[0] == "source":
        return f"{parts[2]}.{parts[3]}"
    name = parts[-1]
    return f"{'staging' if name.startswith('stg_') else 'marts'}.{name}"


def _attached(node: Any) -> str | None:
    attached = getattr(node, "attached_node", None)
    if attached:
        return _qualified(str(attached))
    refs = getattr(getattr(node, "depends_on", None), "nodes", []) or []
    return _qualified(str(refs[0])) if refs else None


def run_build(*, full_refresh: bool = False, select: str | None = None) -> TransformOutcome:
    cfg = DbtSettings()
    project = cfg.project_dir.resolve()
    args = [
        "build",
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(project),
        "--threads",
        str(cfg.threads),
        # Sin dbt.log: el handler de archivo de dbt queda abierto entre invocaciones del
        # mismo proceso (ResourceWarning) y el registro útil ya va al logging estructurado.
        "--log-level-file",
        "none",
    ]
    if cfg.target_dir is not None:
        # En el contenedor el proyecto es de solo lectura: artefactos y logs van fuera.
        args += ["--target-path", str(cfg.target_dir), "--log-path", str(cfg.target_dir / "logs")]
    if full_refresh:
        args.append("--full-refresh")
    if select:
        args += ["--select", select]

    result = dbtRunner().invoke(args)
    if result.exception is not None:
        # Error antes de ejecutar nodos (perfil, conexión, compilación).
        raise TransformError(f"dbt no pudo ejecutarse: {result.exception}")

    nodes = []
    for item in getattr(result.result, "results", []) or []:
        node = item.node
        nodes.append(
            NodeOutcome(
                unique_id=node.unique_id,
                resource_type=str(node.resource_type),
                name=node.name,
                status=str(item.status),
                failures=item.failures,
                severity=str(getattr(node.config, "severity", "")).lower() or None,
                attached_to=_attached(node),
                message=item.message,
            )
        )
    outcome = TransformOutcome(success=bool(result.success), nodes=nodes)
    logger.info("dbt_build_finished", extra=outcome.as_dict())
    return outcome


def record_test_results(engine: Engine, run_id: uuid.UUID, outcome: TransformOutcome) -> int:
    """Guarda cada prueba de dbt como resultado de calidad ``post_transform``.

    dbt informa cuántas filas violan la regla pero no cuántas se evaluaron; por eso
    ``rows_evaluated`` = ``rows_failed`` y se marca en ``details`` (ver docs/data-model.md).
    """
    rows = []
    for node in outcome.nodes:
        if node.resource_type != "test" or node.status == "skipped":
            continue
        failures = node.failures or 0
        rows.append(
            {
                "run_id": run_id,
                "layer": "post_transform",
                "table_name": node.attached_to or "dbt",
                "rule_name": node.name[:200],
                "severity": "warn" if node.severity == "warn" else "error",
                "rows_evaluated": failures,
                "rows_failed": failures,
                "passed": node.status == "pass",
                "details": {"dbt_status": node.status, "rows_evaluated_unknown": True},
            }
        )
    if rows:
        with engine.begin() as conn:
            conn.execute(quality_results.insert(), rows)
    return len(rows)


def failed_nodes(outcome: TransformOutcome) -> list[str]:
    return [f"{n.resource_type}:{n.name}" for n in outcome.nodes if n.status in FAILED_STATUSES]
