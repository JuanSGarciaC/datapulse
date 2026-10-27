"""Registro de ejecuciones y tareas en ``ops`` (observabilidad propia del pipeline).

Prefect también guarda estados, pero en su propia base y con su propio modelo.
``ops.pipeline_runs``/``ops.task_runs`` son la fuente que consultan la API y el
dashboard: no dependen del orquestador y se pueden auditar con SQL.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Engine, func, insert, update

from datapulse.db.schema import pipeline_runs, task_runs

logger = logging.getLogger(__name__)

Trigger = Literal["schedule", "manual", "api", "test"]
MAX_ERROR_LENGTH = 2000


def _safe_message(exc: BaseException) -> str:
    # Mensaje acotado: suficiente para diagnosticar, sin volcar trazas enteras.
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]


@dataclass(slots=True)
class TaskHandle:
    task_run_id: uuid.UUID
    rows_in: int | None = None
    rows_out: int | None = None


class RunRecorder:
    """Uso: ``with RunRecorder(engine, "ingest_customers") as run: ...``."""

    def __init__(
        self,
        engine: Engine,
        flow_name: str,
        *,
        source_name: str | None = None,
        trigger: Trigger = "manual",
        params: dict[str, Any] | None = None,
        orchestrator_run_id: uuid.UUID | None = None,
    ) -> None:
        self.engine = engine
        self.flow_name = flow_name
        self.source_name = source_name
        self.trigger = trigger
        self.params = params or {}
        self.orchestrator_run_id = orchestrator_run_id
        self.run_id = uuid.uuid4()
        self.rows_read = 0
        self.rows_loaded = 0
        self.rows_rejected = 0
        self.partial = False

    def __enter__(self) -> RunRecorder:
        with self.engine.begin() as conn:
            conn.execute(
                insert(pipeline_runs).values(
                    run_id=self.run_id,
                    flow_name=self.flow_name,
                    source_name=self.source_name,
                    status="running",
                    trigger=self.trigger,
                    params=self.params,
                    orchestrator_run_id=self.orchestrator_run_id,
                )
            )
        logger.info("run_started", extra={"run_id": str(self.run_id), "flow": self.flow_name})
        return self

    def add(
        self, *, read: int = 0, loaded: int = 0, rejected: int = 0, partial: bool = False
    ) -> None:
        self.rows_read += read
        self.rows_loaded += loaded
        self.rows_rejected += rejected
        self.partial = self.partial or partial

    def _finish(self, status: str, exc: BaseException | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(pipeline_runs)
                .where(pipeline_runs.c.run_id == self.run_id)
                .values(
                    status=status,
                    finished_at=func.now(),
                    rows_read=self.rows_read,
                    rows_loaded=self.rows_loaded,
                    rows_rejected=self.rows_rejected,
                    error_type=type(exc).__name__ if exc else None,
                    error_message=_safe_message(exc) if exc else None,
                )
            )
        logger.info(
            "run_finished",
            extra={
                "run_id": str(self.run_id),
                "status": status,
                "rows_loaded": self.rows_loaded,
                "rows_rejected": self.rows_rejected,
            },
        )

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None:
        if exc is not None:
            self._finish("failed", exc)
            return  # la excepción se propaga
        self._finish("partial" if self.partial or self.rows_rejected else "success", None)

    @contextmanager
    def task(self, name: str, *, attempt: int = 1) -> Iterator[TaskHandle]:
        handle = TaskHandle(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(
                insert(task_runs).values(
                    task_run_id=handle.task_run_id,
                    run_id=self.run_id,
                    task_name=name,
                    status="running",
                    attempt=attempt,
                )
            )
        status, error = "success", None
        try:
            yield handle
        except BaseException as exc:
            status, error = "failed", _safe_message(exc)
            raise
        finally:
            with self.engine.begin() as conn:
                conn.execute(
                    update(task_runs)
                    .where(task_runs.c.task_run_id == handle.task_run_id)
                    .values(
                        status=status,
                        finished_at=func.now(),
                        rows_in=handle.rows_in,
                        rows_out=handle.rows_out,
                        error_message=error,
                    )
                )
