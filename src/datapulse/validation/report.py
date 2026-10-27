"""Tipos comunes del resultado de validación."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

SAMPLE_SIZE: Final = 5


class Severity(StrEnum):
    ERROR = "error"  # la fila va a cuarentena
    WARN = "warn"  # se registra, la fila se carga


@dataclass(frozen=True, slots=True)
class RowError:
    rule: str
    field: str | None
    message: str

    def as_dict(self) -> dict[str, str | None]:
        return {"rule": self.rule, "field": self.field, "message": self.message}


@dataclass(slots=True)
class RuleResult:
    """Una regla evaluada sobre un lote: se guarda en ``ops.quality_results``."""

    rule: str
    severity: Severity
    rows_evaluated: int
    rows_failed: int
    sample_keys: list[str] = field(default_factory=list)
    passed: bool | None = None  # None → se deriva de rows_failed
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.passed is None:
            self.passed = self.rows_failed == 0


@dataclass(slots=True)
class QuarantinedRow:
    row_number: int
    record_key: str | None
    payload: dict[str, Any]
    errors: list[RowError]


@dataclass(slots=True)
class ValidationReport:
    source: str
    rows_read: int
    valid: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[QuarantinedRow] = field(default_factory=list)
    results: list[RuleResult] = field(default_factory=list)
    duplicates_dropped: int = 0
    rejected_reason: str | None = None

    @property
    def rejected(self) -> bool:
        return self.rejected_reason is not None

    @property
    def error_rate(self) -> float:
        evaluated = len(self.valid) + len(self.quarantined)
        return len(self.quarantined) / evaluated if evaluated else 0.0

    def failures(self, rule: str) -> int:
        return sum(r.rows_failed for r in self.results if r.rule == rule)

    def enforce_error_budget(self, max_error_rate: float) -> None:
        """Si demasiadas filas fallan, el problema es del archivo, no de filas sueltas."""
        if not self.rejected and self.error_rate > max_error_rate:
            self.rejected_reason = (
                f"tasa de error {self.error_rate:.1%} supera el máximo {max_error_rate:.0%}"
            )
            self.results.append(
                RuleResult(
                    rule="error_budget",
                    severity=Severity.ERROR,
                    rows_evaluated=len(self.valid) + len(self.quarantined),
                    rows_failed=len(self.quarantined),
                    passed=False,
                    details={"max_error_rate": max_error_rate},
                )
            )


def sample(keys: list[str | None]) -> list[str]:
    return [k for k in keys if k is not None][:SAMPLE_SIZE]
