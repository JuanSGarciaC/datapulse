from __future__ import annotations

import copy
from typing import Any

import pytest

from datapulse.validation.orders import validate_order, validate_orders

ORDER: dict[str, Any] = {
    "order_id": "O000001",
    "customer_id": "C00001",
    "order_ts": "2026-01-01T10:00:00Z",
    "status": "paid",
    "currency": "USD",
    "updated_at": "2026-01-01T10:05:00Z",
    "items": [{"line_no": 1, "product_id": 3, "quantity": 2, "unit_price": 9.99}],
}


def with_(path: str, value: object) -> dict[str, Any]:
    order = copy.deepcopy(ORDER)
    if path.startswith("items."):
        order["items"][0][path.removeprefix("items.")] = value
    else:
        order[path] = value
    return order


def test_valid_order_is_typed() -> None:
    report = validate_orders({"orders": [ORDER]})
    [row] = report.valid
    assert row["order_ts"].tzinfo is not None
    assert row["items"] == ORDER["items"]
    assert row["_extra"] == {}
    assert not report.quarantined


@pytest.mark.parametrize(
    ("path", "value", "rule"),
    [
        ("order_id", None, "not_null:order_id"),
        ("order_id", "X1", "format:order_id"),
        ("customer_id", None, "not_null:customer_id"),
        ("customer_id", "", "not_null:customer_id"),
        ("customer_id", 5, "format:customer_id"),
        ("order_ts", "2026-01-01", "type:order_ts"),
        ("order_ts", "2026-01-01T10:00:00", "type:order_ts"),
        ("updated_at", None, "not_null:updated_at"),
        ("status", "lost", "accepted_values:status"),
        ("status", None, "not_null:status"),
        ("currency", "usd", "format:currency"),
        ("items", "x", "type:items"),
        ("items", [], "not_empty:items"),
        ("items", ["x"], "type:items"),
        ("items.quantity", "two", "type:items.quantity"),
        ("items.quantity", True, "type:items.quantity"),
        ("items.quantity", 0, "range:items.quantity"),
        ("items.quantity", -1, "range:items.quantity"),
        ("items.product_id", "3", "type:items.product_id"),
        ("items.product_id", 0, "range:items.product_id"),
        ("items.unit_price", "9.99", "type:items.unit_price"),
        ("items.unit_price", float("nan"), "type:items.unit_price"),
        ("items.unit_price", -0.01, "range:items.unit_price"),
        ("items.line_no", 1.5, "type:items.line_no"),
    ],
)
def test_each_rule(path: str, value: object, rule: str) -> None:
    assert [e.rule for e in validate_order(with_(path, value))] == [rule]


def test_a_rule_counts_once_per_order() -> None:
    order = copy.deepcopy(ORDER)
    order["items"] = [dict(ORDER["items"][0], quantity="x"), dict(ORDER["items"][0], quantity="y")]
    assert [e.rule for e in validate_order(order)] == ["type:items.quantity"]


def test_report_counts_duplicates_and_quarantine() -> None:
    bad = with_("items.quantity", "two")
    report = validate_orders({"orders": [ORDER, ORDER, bad, "not-an-object"]}, max_error_rate=1)
    assert report.duplicates_dropped == 1
    assert report.failures("type:items.quantity") == 1
    assert [q.record_key for q in report.quarantined] == ["O000001", None]
    assert report.quarantined[1].payload == {"value": "not-an-object"}


def test_unknown_fields_go_to_extra_with_warning() -> None:
    report = validate_orders({"orders": [ORDER | {"channel": "web"}]})
    assert report.valid[0]["_extra"] == {"channel": "web"}
    [warn] = [r for r in report.results if r.rule == "schema_change:added_fields"]
    assert warn.details == {"fields": ["channel"]}


@pytest.mark.parametrize("document", [[], {"rows": []}, {"orders": "x"}, None])
def test_document_without_orders_list_is_rejected(document: object) -> None:
    report = validate_orders(document)
    assert report.rejected


def test_error_budget() -> None:
    report = validate_orders({"orders": [ORDER, with_("currency", "x")]}, max_error_rate=0.4)
    assert report.rejected
