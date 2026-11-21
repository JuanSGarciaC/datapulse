"""Lectura y validación de un archivo de fuente, sin tocar la base de datos.

La usan la ingesta y el validador en vivo de la API: un archivo recibe el mismo veredicto
se cargue de verdad o solo se pruebe.
"""

from __future__ import annotations

import io
import json

import polars as pl

from datapulse.validation.contracts import CUSTOMERS
from datapulse.validation.orders import SOURCE as ORDERS_SOURCE
from datapulse.validation.orders import validate_orders
from datapulse.validation.report import RuleResult, Severity, ValidationReport
from datapulse.validation.tabular import validate_tabular


def parse_failure(source: str, message: str) -> ValidationReport:
    report = ValidationReport(source=source, rows_read=0)
    report.rejected_reason = message
    report.results.append(
        RuleResult(
            "parse:file", Severity.ERROR, 0, 0, passed=False, details={"error": message[:500]}
        )
    )
    return report


def validate_customers_csv(data: bytes) -> ValidationReport:
    try:
        data.decode("utf-8")  # estricto: Polars aceptaría bytes inválidos sin avisar
        frame = pl.read_csv(io.BytesIO(data), infer_schema=False, encoding="utf8")
    except (pl.exceptions.PolarsError, UnicodeDecodeError) as exc:
        return parse_failure(CUSTOMERS.source, f"CSV ilegible: {exc}")
    return validate_tabular(CUSTOMERS, frame)


def validate_orders_json(data: bytes) -> ValidationReport:
    try:
        document = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        # RecursionError: un JSON anidado a propósito no debe tumbar el proceso.
        return parse_failure(ORDERS_SOURCE, f"JSON ilegible: {exc}")
    return validate_orders(document)
