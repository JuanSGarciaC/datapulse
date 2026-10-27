"""Hashes de contenido: la base de toda la idempotencia (ADR-0004)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _default(value: object) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    raise TypeError(f"tipo no serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """JSON estable: claves ordenadas, sin espacios. Igual contenido → igual texto."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False
    )


def record_hash(record: dict[str, Any]) -> str:
    """Hash del contenido de negocio; ignora columnas técnicas (``_row_number``…)."""
    business = {k: v for k, v in record.items() if not k.startswith("_") or k == "_extra"}
    return hashlib.sha256(canonical_json(business).encode("utf-8")).hexdigest()


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def set_hash(hashes: Iterable[str]) -> str:
    """Hash de un conjunto de hashes (independiente del orden): identifica un lote de la API."""
    items = sorted(hashes)
    if any(not _HEX64.fullmatch(h) for h in items):
        raise ValueError("set_hash espera hashes sha256 en hexadecimal, no contenido")
    return hashlib.sha256("\n".join(items).encode("ascii")).hexdigest()
