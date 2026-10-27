"""Catálogo versionado: la API devuelve el estado de cada producto “a una fecha”.

Mover el reloj (``as_of``) permite simular que la fuente cambia entre una
ejecución y la siguiente, que es lo que necesita la carga incremental.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Catalog:
    def __init__(self, versions: list[dict[str, Any]]) -> None:
        if not versions:
            raise ValueError("el catálogo está vacío")
        self._versions = sorted(versions, key=lambda v: (v["id"], _ts(v["meta"]["updatedAt"])))

    @classmethod
    def from_file(cls, path: Path) -> Catalog:
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        return cls(payload["versions"])

    @property
    def latest_update(self) -> datetime:
        return max(_ts(v["meta"]["updatedAt"]) for v in self._versions)

    def snapshot(self, as_of: datetime | None) -> list[dict[str, Any]]:
        """Última versión de cada producto con ``updatedAt <= as_of``, ordenado por id."""
        current: dict[int, dict[str, Any]] = {}
        for version in self._versions:
            if as_of is None or _ts(version["meta"]["updatedAt"]) <= as_of:
                current[version["id"]] = version
        return [current[k] for k in sorted(current)]
