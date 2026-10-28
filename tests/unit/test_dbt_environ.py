"""dbt-core copia el .env a os.environ al importarse e invocarse; DataPulse lo confina."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from datapulse.transform import dbt as transform

SENTINEL = "DATAPULSE_LEAK_SENTINEL"


def _dotenv(directory: Path) -> None:
    (directory / ".env").write_text(f"{SENTINEL}=secret-from-dotenv\n", encoding="utf-8")


def test_importing_the_transform_module_does_not_load_dotenv(tmp_path: Path) -> None:
    """En un intérprete limpio: dbt aún no está importado y su efecto sería visible."""
    _dotenv(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != SENTINEL}
    code = (
        "import os, datapulse.transform.dbt as t; t._dbt_runner();"
        f" print('{SENTINEL}' in os.environ)"
    )
    out = subprocess.run(  # noqa: S603 - intérprete actual, código fijo
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_raw_dbt_import_does_leak_which_is_why_the_guard_exists(tmp_path: Path) -> None:
    """Documenta el comportamiento de dbt: si deja de filtrar, esta prueba avisa."""
    _dotenv(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != SENTINEL}
    code = f"import os, dbt.cli.main; print('{SENTINEL}' in os.environ)"
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip().splitlines()[-1] == "True"


def test_guard_restores_added_changed_and_removed_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATAPULSE_KEEP", "original")
    monkeypatch.setenv("DATAPULSE_DROP", "present")
    with transform.environ_guard():
        os.environ["DATAPULSE_KEEP"] = "changed"
        os.environ[SENTINEL] = "added"
        del os.environ["DATAPULSE_DROP"]
    assert os.environ["DATAPULSE_KEEP"] == "original"
    assert os.environ["DATAPULSE_DROP"] == "present"
    assert SENTINEL not in os.environ


def test_run_build_confines_variables_set_during_invoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_during_invoke: list[bool] = []

    class FakeResult:
        exception = None
        success = True
        result = None

    class FakeRunner:
        def invoke(self, args: list[str]) -> Any:
            os.environ[SENTINEL] = "from-dotenv"  # lo que hace load_dotenv dentro de dbt
            seen_during_invoke.append(SENTINEL in os.environ)
            return FakeResult()

    monkeypatch.setattr(transform, "_dbt_runner", FakeRunner)
    outcome = transform.run_build()
    assert outcome.success
    assert seen_during_invoke == [True]  # dbt sí ve las variables mientras corre
    assert SENTINEL not in os.environ  # pero no sobreviven a la llamada
