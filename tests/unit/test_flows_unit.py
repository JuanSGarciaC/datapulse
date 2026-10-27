"""Partes de la orquestación que no necesitan servidor: política de reintentos, descubrimiento."""

from __future__ import annotations

from pathlib import Path

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
