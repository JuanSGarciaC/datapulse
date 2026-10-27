from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal

import polars as pl
import pytest

from datapulse.validation.contracts import CUSTOMERS, PRODUCTS, flatten_product
from datapulse.validation.report import Severity
from datapulse.validation.tabular import ColumnSpec, Kind, TabularContract, validate_tabular

CONTRACT = TabularContract(
    source="test",
    key="id",
    columns=(
        ColumnSpec("id", required=True, pattern=r"^K\d+$"),
        ColumnSpec("amount", kind=Kind.DECIMAL, min_value=0, max_value=100),
        ColumnSpec("qty", kind=Kind.INT),
        ColumnSpec("day", kind=Kind.DATE),
        ColumnSpec("ts", kind=Kind.DATETIME, required=True, target="event_ts"),
        ColumnSpec("color", allowed=frozenset({"red", "blue"})),
        ColumnSpec("note"),
    ),
    max_error_rate=1.0,
)
GOOD: dict[str, str | None] = {
    "id": "K1",
    "amount": "12.10",
    "qty": "3",
    "day": "2026-01-02",
    "ts": "2026-01-02T03:04:05Z",
    "color": "red",
    "note": "ok",
}


def frame(*rows: Mapping[str, str | None]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=dict.fromkeys(rows[0], pl.String))


def failed(report: object, rule: str) -> int:
    return report.failures(rule)  # type: ignore[attr-defined, no-any-return]


def test_valid_row_is_typed_exactly() -> None:
    report = validate_tabular(CONTRACT, frame(GOOD))
    assert not report.quarantined
    row = report.valid[0]
    assert row["amount"] == Decimal("12.10")  # desde texto: sin errores de coma flotante
    assert row["qty"] == 3
    assert row["day"] == date(2026, 1, 2)
    assert row["event_ts"] == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert "ts" not in row
    assert row["_row_number"] == 1
    assert row["_extra"] == {}


@pytest.mark.parametrize(
    ("override", "rule"),
    [
        ({"id": None}, "not_null:id"),
        ({"id": "   "}, "not_null:id"),
        ({"id": "X9"}, "format:id"),
        ({"amount": "abc"}, "type:amount"),
        ({"amount": "inf"}, "type:amount"),
        ({"amount": "-1"}, "range:amount"),
        ({"amount": "100.01"}, "range:amount"),
        ({"qty": "3.5"}, "type:qty"),
        ({"day": "31/02/2026"}, "type:day"),
        ({"day": "2026-02-30"}, "type:day"),
        ({"ts": "2026-01-02"}, "type:ts"),
        ({"ts": "2026-01-02T03:04:05"}, "type:ts"),
        ({"color": "green"}, "accepted_values:color"),
    ],
)
def test_each_rule_sends_the_row_to_quarantine(override: dict[str, str | None], rule: str) -> None:
    report = validate_tabular(CONTRACT, frame(GOOD, GOOD | {"id": "K2"} | override))
    assert failed(report, rule) == 1
    assert len(report.valid) == 1
    [bad] = report.quarantined
    assert bad.row_number == 2
    assert [e.rule for e in bad.errors] == [rule]
    assert bad.payload["note"] == "ok"  # el payload original se conserva


def test_optional_empty_values_are_null_not_errors() -> None:
    report = validate_tabular(CONTRACT, frame(GOOD | {"amount": "", "color": None, "day": " "}))
    assert not report.quarantined
    assert report.valid[0]["amount"] is None


def test_exact_duplicates_are_dropped_and_reported() -> None:
    report = validate_tabular(CONTRACT, frame(GOOD, GOOD, GOOD | {"id": "K2"}))
    assert report.duplicates_dropped == 1
    assert failed(report, "duplicate_in_batch") == 1
    assert [r["id"] for r in report.valid] == ["K1", "K2"]
    assert [r["_row_number"] for r in report.valid] == [1, 3]


def test_missing_required_column_rejects_the_batch() -> None:
    data = frame({k: v for k, v in GOOD.items() if k != "ts"})
    report = validate_tabular(CONTRACT, data)
    assert report.rejected
    assert "ts" in str(report.rejected_reason)
    assert report.valid == []


def test_schema_drift_is_tolerated_and_extra_columns_kept() -> None:
    data = frame({k: v for k, v in GOOD.items() if k != "note"} | {"loyalty": "gold"})
    report = validate_tabular(CONTRACT, data)
    rules = {r.rule: r for r in report.results}
    assert rules["schema_change:missing_columns"].severity is Severity.WARN
    assert rules["schema_change:added_columns"].details == {"columns": ["loyalty"]}
    assert rules["schema_change:added_columns"].passed is False
    assert report.valid[0]["note"] is None
    assert report.valid[0]["_extra"] == {"loyalty": "gold"}


def test_error_budget_rejects_a_mostly_broken_file() -> None:
    strict = TabularContract("t", "id", CONTRACT.columns, max_error_rate=0.4)
    report = validate_tabular(strict, frame(GOOD, GOOD | {"id": None}, GOOD | {"id": "bad"}))
    assert report.rejected
    assert "supera" in str(report.rejected_reason)
    assert any(r.rule == "error_budget" for r in report.results)


def test_sample_keys_are_capped() -> None:
    rows = [GOOD | {"id": f"K{i}", "color": "green"} for i in range(10)]
    report = validate_tabular(CONTRACT, frame(*rows))
    [rule] = [r for r in report.results if r.rule == "accepted_values:color"]
    assert rule.rows_failed == 10
    assert rule.sample_keys == ["K0", "K1", "K2", "K3", "K4"]


def test_extras_follow_their_row_after_deduplication() -> None:
    report = validate_tabular(
        CONTRACT,
        frame(GOOD, GOOD, GOOD | {"id": "K2"}),
        extras=[{"n": 0}, {"n": 1}, {"n": 2}],
    )
    assert [r["_extra"] for r in report.valid] == [{"n": 0}, {"n": 2}]


def test_customers_contract_matches_generator_columns() -> None:
    from datapulse.synthetic.generator import CUSTOMER_COLUMNS  # noqa: PLC0415

    assert CUSTOMERS.names == CUSTOMER_COLUMNS


def test_flatten_product_moves_meta_and_keeps_everything_else() -> None:
    known, extra = flatten_product(
        {
            "id": 7,
            "title": "T",
            "price": 9.5,
            "images": ["a.webp"],
            "meta": {"createdAt": "c", "updatedAt": "u", "barcode": "123"},
        }
    )
    assert known["id"] == "7"
    assert known["price"] == "9.5"
    assert known["updatedAt"] == "u"
    assert known["brand"] is None
    assert extra == {"images": ["a.webp"], "meta": {"barcode": "123"}}
    assert set(known) == set(PRODUCTS.names)
