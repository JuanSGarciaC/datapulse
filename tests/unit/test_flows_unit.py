"""Partes de la orquestación que no necesitan servidor: política de reintentos, descubrimiento."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from prefect.states import Completed, Failed

from datapulse.flows.__main__ import deployments, main
from datapulse.flows.pipeline import discover, is_transient, seed_landing
from datapulse.ingestion.load import ConcurrentIngestionError
from datapulse.ingestion.rest import ContractViolationError, SourceError
from tests.conftest import ROOT


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (SourceError("caída"), True),
        (ConnectionError("red"), True),
        (ConcurrentIngestionError("otra carga"), True),
        (ContractViolationError("contrato"), False),
        (ValueError("bug"), False),
        (KeyError("x"), False),
    ],
)
def test_only_transient_failures_are_retried(exc: Exception, expected: bool) -> None:
    assert is_transient(None, None, Failed(data=exc)) is expected  # type: ignore[arg-type]


def test_completed_state_counts_as_transient_noop() -> None:
    assert is_transient(None, None, Completed(data=1)) is True  # type: ignore[arg-type]


def test_seed_and_discover_in_chronological_order(tmp_path: Path) -> None:
    copied = seed_landing(ROOT / "data/samples", tmp_path)
    assert len(copied) == 7
    names = [p.name for p in discover(tmp_path, "customers")]
    assert names == sorted(names)
    assert names[0] == "customers_2026-01-01.csv"
    assert discover(tmp_path / "nada", "orders") == []


def test_cli_seed_landing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["seed-landing", "--landing", str(tmp_path)]) == 0
    assert "orders_batch_003.json" in capsys.readouterr().out


def test_deployments_have_schedules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAPULSE_FLOW_DAILY_CRON", "30 5 * * *")
    daily, products = deployments()
    assert daily.name == "daily"
    assert daily.schedules[0].schedule.cron == "30 5 * * *"
    assert products.parameters == {"trigger": "schedule"}


def test_dbt_nodes_are_qualified_with_their_schema() -> None:
    from datapulse.transform.dbt import _qualified  # noqa: PLC0415

    assert _qualified("model.datapulse.stg_orders") == "staging.stg_orders"
    assert _qualified("model.datapulse.fct_order_items") == "marts.fct_order_items"
    assert _qualified("source.datapulse.raw.orders") == "raw.orders"


def test_dbt_configuration_errors_raise_transform_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datapulse.transform.dbt import TransformError, run_build  # noqa: PLC0415

    monkeypatch.setenv("DATAPULSE_DBT_PROJECT_DIR", str(tmp_path / "no-existe"))
    monkeypatch.setenv("DATAPULSE_DBT_TARGET_DIR", str(tmp_path / "target"))
    with pytest.raises(TransformError, match="dbt no pudo ejecutarse"):
        run_build()


def test_transform_errors_are_not_retried() -> None:
    from datapulse.transform.dbt import TransformError  # noqa: PLC0415

    state: Any = Failed(data=TransformError("prueba en error"))
    assert is_transient(None, None, state) is False  # type: ignore[arg-type]
