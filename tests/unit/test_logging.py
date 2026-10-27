from __future__ import annotations

import json
import logging
import sys

import pytest

from datapulse.config import LogFormat
from datapulse.observability.logging import JsonFormatter, configure_logging


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        "datapulse.test", logging.INFO, __file__, 1, "hola %s", ("mundo",), None
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_parseable_line_with_context() -> None:
    line = JsonFormatter().format(_record(run_id="r-1", rows=10))
    payload = json.loads(line)
    assert "\n" not in line
    assert payload["msg"] == "hola mundo"
    assert payload["level"] == "INFO"
    assert payload["run_id"] == "r-1"
    assert payload["rows"] == 10


def test_sensitive_keys_are_redacted() -> None:
    payload = json.loads(JsonFormatter().format(_record(password="x", api_key="y", token="z")))
    assert payload["password"] == payload["api_key"] == payload["token"] == "***"


def test_exceptions_include_type() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "fallo", (), sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert payload["exc_type"] == "ValueError"
    assert "boom" in payload["exc"]


def test_configure_logging_is_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", LogFormat.JSON)
    configure_logging("INFO", LogFormat.JSON)
    assert len(logging.getLogger().handlers) == 1
    logging.getLogger("x").info("evento")
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert json.loads(out[0])["msg"] == "evento"
