"""Validación de pedidos (JSON anidado: cabecera + líneas).

Para documentos anidados se usan validadores Python explícitos en lugar de
Polars: aplanar primero obligaría a inferir un tipo para ``quantity`` y el
valor ``"two"`` desaparecería antes de poder detectarlo (ADR-0007).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Final, TypeGuard

from datapulse.synthetic import vocab
from datapulse.validation.report import (
    QuarantinedRow,
    RowError,
    RuleResult,
    Severity,
    ValidationReport,
    sample,
)

SOURCE: Final = "orders_json"
HEADER_FIELDS: Final = (
    "order_id",
    "customer_id",
    "order_ts",
    "status",
    "currency",
    "updated_at",
    "items",
)
ITEM_FIELDS: Final = ("line_no", "product_id", "quantity", "unit_price")
RULES: Final = (
    "not_null:order_id",
    "format:order_id",
    "not_null:customer_id",
    "format:customer_id",
    "not_null:order_ts",
    "type:order_ts",
    "not_null:updated_at",
    "type:updated_at",
    "not_null:status",
    "accepted_values:status",
    "format:currency",
    "type:items",
    "not_empty:items",
    "type:items.line_no",
    "type:items.product_id",
    "range:items.product_id",
    "type:items.quantity",
    "range:items.quantity",
    "type:items.unit_price",
    "range:items.unit_price",
)
_ORDER_ID = re.compile(r"^O\d{6}$")
_CUSTOMER_ID = re.compile(r"^C\d{5}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and value == value  # noqa: PLR0124 - descarta NaN


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _check_header(order: dict[str, Any], add: Any) -> None:
    for name, pattern in (("order_id", _ORDER_ID), ("customer_id", _CUSTOMER_ID)):
        value = order.get(name)
        if _blank(value):
            add(f"not_null:{name}", name, "valor obligatorio ausente")
        elif not isinstance(value, str) or not pattern.match(value):
            add(f"format:{name}", name, f"no cumple {pattern.pattern}")
    for name in ("order_ts", "updated_at"):
        value = order.get(name)
        if _blank(value):
            add(f"not_null:{name}", name, "valor obligatorio ausente")
        elif parse_ts(value) is None:
            add(f"type:{name}", name, "no es fecha-hora RFC 3339 con zona")
    status = order.get("status")
    if _blank(status):
        add("not_null:status", "status", "valor obligatorio ausente")
    elif status not in vocab.ORDER_STATUSES:
        add("accepted_values:status", "status", f"valor no permitido: {status!r}")
    currency = order.get("currency")
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        add("format:currency", "currency", "se esperaba código ISO 4217")


def _check_items(items: object, add: Any) -> None:
    if not isinstance(items, list):
        add("type:items", "items", "se esperaba una lista")
        return
    if not items:
        add("not_empty:items", "items", "el pedido no tiene líneas")
        return
    for item in items:
        if not isinstance(item, dict):
            add("type:items", "items", "cada línea debe ser un objeto")
            continue
        if not _is_int(item.get("line_no")):
            add("type:items.line_no", "items.line_no", "se esperaba entero")
        product_id = item.get("product_id")
        if not _is_int(product_id):
            add("type:items.product_id", "items.product_id", "se esperaba entero")
        elif product_id < 1:
            add("range:items.product_id", "items.product_id", "debe ser >= 1")
        quantity = item.get("quantity")
        if not _is_int(quantity):
            add("type:items.quantity", "items.quantity", f"se esperaba entero, llegó {quantity!r}")
        elif quantity <= 0:
            add("range:items.quantity", "items.quantity", f"debe ser > 0, llegó {quantity}")
        price = item.get("unit_price")
        if not _is_number(price):
            add("type:items.unit_price", "items.unit_price", "se esperaba número")
        elif price < 0:
            add("range:items.unit_price", "items.unit_price", "debe ser >= 0")


def validate_order(order: object) -> list[RowError]:
    errors: list[RowError] = []
    seen: set[str] = set()

    def add(rule: str, field: str, message: str) -> None:
        if rule not in seen:  # una regla cuenta una vez por pedido
            seen.add(rule)
            errors.append(RowError(rule, field, message))

    if not isinstance(order, dict):
        add("type:order", "order", "se esperaba un objeto")
        return errors
    _check_header(order, add)
    _check_items(order.get("items"), add)
    return errors


def validate_orders(document: object, *, max_error_rate: float = 0.2) -> ValidationReport:
    orders = document.get("orders") if isinstance(document, dict) else None
    if not isinstance(orders, list):
        report = ValidationReport(source=SOURCE, rows_read=0)
        report.rejected_reason = "el documento no contiene una lista 'orders'"
        report.results.append(RuleResult("schema:orders_list", Severity.ERROR, 0, 0, passed=False))
        return report

    report = ValidationReport(source=SOURCE, rows_read=len(orders))
    seen_docs: set[str] = set()
    unique: list[tuple[int, object]] = []
    dropped_keys: list[str | None] = []
    for row_number, order in enumerate(orders, start=1):
        canonical = json.dumps(order, sort_keys=True, default=str)
        if canonical in seen_docs:
            dropped_keys.append(order.get("order_id") if isinstance(order, dict) else None)
            continue
        seen_docs.add(canonical)
        unique.append((row_number, order))
    report.duplicates_dropped = len(dropped_keys)
    report.results.append(
        RuleResult(
            "duplicate_in_batch",
            Severity.WARN,
            len(orders),
            len(dropped_keys),
            sample_keys=sample(dropped_keys),
        )
    )

    failures: dict[str, list[str | None]] = defaultdict(list)
    unknown_fields: set[str] = set()
    for row_number, order in unique:
        errors = validate_order(order)
        key = order.get("order_id") if isinstance(order, dict) else None
        for error in errors:
            failures[error.rule].append(key)
        if errors:
            payload = order if isinstance(order, dict) else {"value": order}
            report.quarantined.append(QuarantinedRow(row_number, key, payload, errors))
            continue
        if not isinstance(order, dict):  # pragma: no cover - validate_order ya lo marcó
            continue
        unknown_fields |= set(order) - set(HEADER_FIELDS)
        record = {
            "order_id": order["order_id"],
            "customer_id": order["customer_id"],
            "order_ts": parse_ts(order["order_ts"]),
            "status": order["status"],
            "currency": order["currency"],
            "updated_at": parse_ts(order["updated_at"]),
            "items": [{f: item[f] for f in ITEM_FIELDS} for item in order["items"]],
            "_extra": {k: v for k, v in order.items() if k not in HEADER_FIELDS},
            "_row_number": row_number,
        }
        report.valid.append(record)

    for rule in RULES:
        keys = failures.get(rule, [])
        report.results.append(
            RuleResult(rule, Severity.ERROR, len(unique), len(keys), sample_keys=sample(keys))
        )
    if unknown_fields:
        report.results.append(
            RuleResult(
                "schema_change:added_fields",
                Severity.WARN,
                len(unique),
                0,
                passed=False,
                details={"fields": sorted(unknown_fields)},
            )
        )
    report.enforce_error_budget(max_error_rate)
    return report
