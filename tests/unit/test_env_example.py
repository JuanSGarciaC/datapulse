"""`.env.example` debe poder cargarse desde shell, Docker Compose, pydantic y PowerShell."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from tests.conftest import ROOT

ENV_EXAMPLE = ROOT / ".env.example"
_LINE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")


def _assignments() -> list[tuple[str, str]]:
    pairs = []
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        assert match, f"línea no válida en .env.example: {raw!r}"
        pairs.append((match["key"], match["value"]))
    return pairs


def test_unquoted_values_have_no_spaces() -> None:
    """Regresión: ``X=0 6 * * *`` sin comillas ejecuta ``6`` al hacer ``. ./.env``.

    Así falló el job de Docker de la fase 4 (código 127).
    """
    for key, value in _assignments():
        unquoted = re.split(r"\s+#", value, maxsplit=1)[0]
        if unquoted.startswith(('"', "'")):
            continue
        assert " " not in unquoted.strip(), f"{key}: valor con espacios sin comillas"


def test_no_real_secrets_in_example() -> None:
    for key, value in _assignments():
        if any(word in key for word in ("PASSWORD", "TOKEN", "KEY")):
            assert value.split()[0].startswith("change-me"), (
                f"{key} debe ser un marcador change-me-*"
            )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash no disponible")
def test_bash_can_source_it_strictly() -> None:
    result = subprocess.run(  # noqa: S603 - comando fijo, sin entrada externa
        ["bash", "-ec", f"set -a; . '{ENV_EXAMPLE}'; set +a"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
