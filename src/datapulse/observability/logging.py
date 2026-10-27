"""Logging estructurado en JSON (una línea por evento) para que sea consultable.

Se usa la librería estándar en lugar de una dependencia externa: basta con un
formateador y permite añadir contexto (``run_id``, ``source``...) vía ``extra=``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Final

from datapulse.config import LogFormat

# Atributos estándar de LogRecord: todo lo demás se considera contexto de negocio.
_RESERVED: Final = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"message", "asctime"}
)
# Claves que nunca deben escribirse en un log aunque se pasen por error en ``extra``.
_REDACTED_KEYS: Final = frozenset({"password", "secret", "token", "api_key", "authorization"})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = "***" if key.lower() in _REDACTED_KEYS else value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: LogFormat = LogFormat.JSON) -> None:
    """Configura el logger raíz de forma idempotente (se puede llamar varias veces)."""
    handler = logging.StreamHandler(sys.stdout)
    if fmt is LogFormat.JSON:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
