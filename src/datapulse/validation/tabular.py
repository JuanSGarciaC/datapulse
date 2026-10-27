"""Validación vectorizada (Polars) de datos tabulares que llegan como texto.

Todo entra como ``String`` y se convierte con reglas explícitas. Así un valor
como ``"31/02/2026"`` se detecta como error de tipo en lugar de que una
librería lo convierta en silencio (ADR-0002).

Orden de evaluación:
1. Esquema: falta columna obligatoria → lote rechazado; columna nueva → aviso.
2. Filas idénticas dentro del lote → se conserva la primera (aviso).
3. Reglas por columna (obligatorio, tipo, formato, valores permitidos, rango).
4. Filas con algún error → cuarentena; el resto se tipa y se devuelve.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

import polars as pl

from datapulse.validation.report import (
    QuarantinedRow,
    RowError,
    RuleResult,
    Severity,
    ValidationReport,
    sample,
)

ROW: Final = "__row"
IDX: Final = "__idx"


class Kind(StrEnum):
    TEXT = "text"
    INT = "int"
    DECIMAL = "decimal"
    DATE = "date"
    DATETIME = "datetime"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str
    kind: Kind = Kind.TEXT
    required: bool = False
    pattern: str | None = None
    allowed: frozenset[str] | None = None
    min_value: float | None = None
    max_value: float | None = None
    target: str | None = None  # nombre de la columna en raw, si difiere

    @property
    def output(self) -> str:
        return self.target or self.name


@dataclass(frozen=True, slots=True)
class TabularContract:
    source: str
    key: str
    columns: tuple[ColumnSpec, ...]
    max_error_rate: float = 0.2

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


def _parsed(spec: ColumnSpec) -> pl.Expr:
    col = pl.col(spec.name)
    match spec.kind:
        case Kind.INT:
            return col.cast(pl.Int64, strict=False)
        case Kind.DECIMAL:
            as_float = col.cast(pl.Float64, strict=False)
            return pl.when(as_float.is_finite()).then(as_float).otherwise(None)
        case Kind.DATE:
            return col.str.strptime(pl.Date, "%Y-%m-%d", strict=False)
        case Kind.DATETIME:
            # %+ = RFC 3339: exige zona horaria; una fecha sin hora no es válida.
            return col.str.to_datetime(format="%+", strict=False, time_unit="us", time_zone="UTC")
        case _:
            return col


def _checks(spec: ColumnSpec) -> list[tuple[str, pl.Expr, str]]:
    """(regla, máscara de fallo, mensaje) para una columna."""
    col = pl.col(spec.name)
    present = col.is_not_null()
    checks: list[tuple[str, pl.Expr, str]] = []
    if spec.required:
        checks.append((f"not_null:{spec.name}", col.is_null(), "valor obligatorio ausente"))
    if spec.kind is not Kind.TEXT:
        checks.append(
            (f"type:{spec.name}", present & _parsed(spec).is_null(), f"no es {spec.kind}")
        )
    if spec.pattern is not None:
        checks.append(
            (
                f"format:{spec.name}",
                present & ~col.str.contains(spec.pattern),
                f"no cumple el patrón {spec.pattern}",
            )
        )
    if spec.allowed is not None:
        checks.append(
            (
                f"accepted_values:{spec.name}",
                present & ~col.is_in(sorted(spec.allowed)),
                f"valor no permitido (permitidos: {sorted(spec.allowed)})",
            )
        )
    if spec.min_value is not None or spec.max_value is not None:
        value = _parsed(spec).cast(pl.Float64)
        too_low = value < spec.min_value if spec.min_value is not None else pl.lit(False)
        too_high = value > spec.max_value if spec.max_value is not None else pl.lit(False)
        checks.append(
            (
                f"range:{spec.name}",
                value.is_not_null() & (too_low | too_high),
                f"fuera de rango [{spec.min_value}, {spec.max_value}]",
            )
        )
    return checks


def _typed(spec: ColumnSpec, raw: str | None, parsed: Any) -> Any:
    if raw is None:
        return None
    match spec.kind:
        case Kind.DECIMAL:
            return Decimal(raw)  # desde el texto original: sin errores de coma flotante
        case Kind.INT:
            return int(parsed)
        case Kind.DATE | Kind.DATETIME:
            if not isinstance(
                parsed, date | datetime
            ):  # pragma: no cover - lo impide la regla type:
                raise TypeError(f"{spec.name}: se esperaba fecha, llegó {type(parsed).__name__}")
            return parsed
        case _:
            return raw


def validate_tabular(
    contract: TabularContract,
    frame: pl.DataFrame,
    *,
    extras: list[dict[str, Any]] | None = None,
) -> ValidationReport:
    """Valida ``frame`` (columnas de texto). ``extras`` acompaña a cada fila tal cual."""
    report = ValidationReport(source=contract.source, rows_read=frame.height)
    columns = set(frame.columns)
    specs = contract.columns

    missing_required = [c.name for c in specs if c.required and c.name not in columns]
    missing_optional = [c.name for c in specs if not c.required and c.name not in columns]
    added = sorted(columns - set(contract.names))
    if missing_required:
        report.rejected_reason = f"faltan columnas obligatorias: {missing_required}"
        report.results.append(
            RuleResult(
                "schema_change:missing_required_columns",
                Severity.ERROR,
                frame.height,
                frame.height,
                details={"columns": missing_required},
            )
        )
        return report
    if missing_optional:
        report.results.append(
            RuleResult(
                "schema_change:missing_columns",
                Severity.WARN,
                frame.height,
                0,
                passed=False,
                details={"columns": missing_optional},
            )
        )
    if added:
        report.results.append(
            RuleResult(
                "schema_change:added_columns",
                Severity.WARN,
                frame.height,
                0,
                passed=False,
                details={"columns": added},
            )
        )

    df = frame.with_columns(
        *[pl.lit(None, dtype=pl.String).alias(n) for n in missing_optional],
    ).with_columns(
        # Espacios alrededor y cadenas vacías cuentan como ausencia de valor.
        *[pl.col(n).cast(pl.String).str.strip_chars().replace("", None) for n in contract.names],
        *[pl.col(n).cast(pl.String) for n in added],
        pl.int_range(1, frame.height + 1).alias(ROW),
        pl.int_range(0, frame.height).alias(IDX),
    )

    # Filas idénticas (todas las columnas de origen) dentro del mismo lote.
    source_cols = [*contract.names, *added]
    first = df.select(pl.struct(source_cols).is_first_distinct()).to_series()
    dropped = df.filter(~first)
    report.duplicates_dropped = dropped.height
    report.results.append(
        RuleResult(
            "duplicate_in_batch",
            Severity.WARN,
            df.height,
            dropped.height,
            sample_keys=sample(dropped[contract.key].to_list()),
        )
    )
    df = df.filter(first)

    checks = [check for spec in specs for check in _checks(spec)]
    flags = df.select(
        pl.col(ROW),
        *[mask.fill_null(value=False).alias(rule) for rule, mask, _ in checks],
    )
    keys = df[contract.key].to_list()
    for rule, _, _ in checks:
        failed = flags[rule].to_list()
        report.results.append(
            RuleResult(
                rule,
                Severity.ERROR,
                df.height,
                sum(failed),
                sample_keys=sample([k for k, bad in zip(keys, failed, strict=True) if bad]),
            )
        )

    parsed = df.select(
        *[_parsed(spec).alias(spec.name) for spec in specs if spec.kind is not Kind.TEXT]
    )
    messages = {rule: message for rule, _, message in checks}
    fields = {rule: rule.split(":", 1)[1] for rule, _, _ in checks}
    raw_rows = df.select([*contract.names, *added]).to_dicts()
    flag_rows = flags.drop(ROW).to_dicts()
    parsed_rows = parsed.to_dicts()
    row_numbers = df[ROW].to_list()
    indexes = df[IDX].to_list()

    for i, raw in enumerate(raw_rows):
        errors = [
            RowError(rule, fields[rule], messages[rule])
            for rule, bad in flag_rows[i].items()
            if bad
        ]
        if errors:
            report.quarantined.append(
                QuarantinedRow(row_numbers[i], raw.get(contract.key), raw, errors)
            )
            continue
        record: dict[str, Any] = {
            spec.output: _typed(spec, raw[spec.name], parsed_rows[i].get(spec.name))
            for spec in specs
        }
        extra = {name: raw[name] for name in added}
        if extras is not None:
            extra |= extras[indexes[i]]
        record["_extra"] = extra
        record["_row_number"] = row_numbers[i]
        report.valid.append(record)

    report.enforce_error_budget(contract.max_error_rate)
    return report


def is_finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
