"""Consultas de solo lectura.

Todas las consultas son **literales completos** con parámetros enlazados: no se
interpola ningún fragmento SQL, ni siquiera constantes. Los filtros opcionales se
expresan como ``(:param IS NULL OR columna = :param)`` y el total de cada página
se obtiene con ``count(*) OVER ()`` en la misma consulta.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Final, Literal

from sqlalchemy import Connection, text

_KPIS: Final = text("""
    SELECT
        coalesce(sum(line_amount) FILTER (WHERE is_revenue), 0)::numeric(14,2) AS revenue,
        count(DISTINCT order_id) FILTER (WHERE is_revenue) AS orders,
        coalesce(sum(quantity) FILTER (WHERE is_revenue), 0) AS units,
        count(DISTINCT customer_id) FILTER (WHERE is_revenue) AS active_customers,
        count(DISTINCT order_id) FILTER (WHERE NOT is_revenue) AS cancelled_or_returned_orders
    FROM marts.fct_order_items
    WHERE (CAST(:date_from AS date) IS NULL OR order_date >= CAST(:date_from AS date))
      AND (CAST(:date_to AS date) IS NULL OR order_date <= CAST(:date_to AS date))
""")

_DAILY_SALES: Final = text("""
    SELECT order_date, category, orders, units, revenue, cancelled_or_returned_orders,
           count(*) OVER () AS _total
    FROM marts.fct_daily_sales
    WHERE (CAST(:date_from AS date) IS NULL OR order_date >= CAST(:date_from AS date))
      AND (CAST(:date_to AS date) IS NULL OR order_date <= CAST(:date_to AS date))
      AND (CAST(:category AS text) IS NULL OR category = CAST(:category AS text))
    ORDER BY order_date, category
    LIMIT :limit OFFSET :offset
""")

_BY_CATEGORY: Final = text("""
    SELECT category, sum(orders)::int AS orders, sum(units)::int AS units,
           sum(revenue)::numeric(14,2) AS revenue
    FROM marts.fct_daily_sales
    WHERE (CAST(:date_from AS date) IS NULL OR order_date >= CAST(:date_from AS date))
      AND (CAST(:date_to AS date) IS NULL OR order_date <= CAST(:date_to AS date))
    GROUP BY category
    ORDER BY revenue DESC, category
""")

_TOP_PRODUCTS_SELECT: Final = """
    SELECT f.product_id, p.title, p.category,
           sum(f.quantity)::int AS units, sum(f.line_amount)::numeric(14,2) AS revenue
    FROM marts.fct_order_items f
    LEFT JOIN marts.dim_product p USING (product_id)
    WHERE f.is_revenue
      AND (CAST(:date_from AS date) IS NULL OR f.order_date >= CAST(:date_from AS date))
      AND (CAST(:date_to AS date) IS NULL OR f.order_date <= CAST(:date_to AS date))
    GROUP BY f.product_id, p.title, p.category
"""
_TOP_PRODUCTS: Final = {
    "revenue": text(_TOP_PRODUCTS_SELECT + " ORDER BY revenue DESC, f.product_id LIMIT :limit"),
    "units": text(_TOP_PRODUCTS_SELECT + " ORDER BY units DESC, f.product_id LIMIT :limit"),
}

_RUNS: Final = text("""
    SELECT run_id, flow_name, source_name, status, trigger, started_at, finished_at,
           extract(epoch FROM finished_at - started_at)::float AS duration_s,
           rows_read, rows_loaded, rows_rejected, error_type, error_message,
           count(*) OVER () AS _total
    FROM ops.pipeline_runs
    WHERE (CAST(:status AS text) IS NULL OR status = CAST(:status AS text))
      AND (CAST(:flow AS text) IS NULL OR flow_name = CAST(:flow AS text))
    ORDER BY started_at DESC
    LIMIT :limit OFFSET :offset
""")

_RUN: Final = text("""
    SELECT run_id, flow_name, source_name, status, trigger, started_at, finished_at,
           extract(epoch FROM finished_at - started_at)::float AS duration_s,
           rows_read, rows_loaded, rows_rejected, error_type, error_message,
           orchestrator_run_id, params
    FROM ops.pipeline_runs WHERE run_id = :id
""")

_RUN_TASKS: Final = text("""
    SELECT task_name, status, attempt, started_at, finished_at,
           extract(epoch FROM finished_at - started_at)::float AS duration_s,
           rows_in, rows_out, error_message
    FROM ops.task_runs WHERE run_id = :id
    ORDER BY started_at, attempt
""")

_RUN_QUALITY_FAILURES: Final = text("""
    SELECT coalesce(sum(rows_failed), 0) FROM ops.quality_results
    WHERE run_id = :id AND NOT passed
""")

_QUALITY_SUMMARY: Final = text("""
    SELECT layer, table_name, rule_name, max(severity) AS severity,
           count(*) AS checks,
           sum(rows_evaluated)::bigint AS rows_evaluated,
           sum(rows_failed)::bigint AS rows_failed,
           max(checked_at) AS last_checked_at,
           (array_agg(passed ORDER BY checked_at DESC, id DESC))[1] AS last_passed
    FROM ops.quality_results
    WHERE (CAST(:since AS timestamptz) IS NULL OR checked_at >= CAST(:since AS timestamptz))
      AND (CAST(:layer AS text) IS NULL OR layer = CAST(:layer AS text))
    GROUP BY layer, table_name, rule_name
    ORDER BY sum(rows_failed) DESC, layer, table_name, rule_name
""")

_QUALITY_RESULTS: Final = text("""
    SELECT id, run_id, batch_id, layer, table_name, rule_name, severity, rows_evaluated,
           rows_failed, passed, details, checked_at, count(*) OVER () AS _total
    FROM ops.quality_results
    WHERE (CAST(:table_name AS text) IS NULL OR table_name = CAST(:table_name AS text))
      AND (CAST(:passed AS boolean) IS NULL OR passed = CAST(:passed AS boolean))
    ORDER BY checked_at DESC, id DESC
    LIMIT :limit OFFSET :offset
""")

_BATCHES: Final = text("""
    SELECT batch_id, source_name, source_ref, content_sha256, status, row_count,
           rejected_count, run_id, created_at, updated_at, count(*) OVER () AS _total
    FROM ops.ingestion_registry
    WHERE (CAST(:source AS text) IS NULL OR source_name = CAST(:source AS text))
      AND (CAST(:status AS text) IS NULL OR status = CAST(:status AS text))
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
""")

_VOLUMES: Final = text("""
    SELECT (started_at AT TIME ZONE 'UTC')::date AS day, flow_name,
           count(*)::int AS runs,
           count(*) FILTER (WHERE status = 'failed')::int AS failed_runs,
           sum(rows_read)::bigint AS rows_read,
           sum(rows_loaded)::bigint AS rows_loaded,
           sum(rows_rejected)::bigint AS rows_rejected,
           avg(extract(epoch FROM finished_at - started_at))::float AS avg_duration_s,
           max(extract(epoch FROM finished_at - started_at))::float AS max_duration_s
    FROM ops.pipeline_runs
    WHERE started_at >= now() - make_interval(days => :days)
    GROUP BY 1, 2
    ORDER BY 1, 2
""")


def _rows(conn: Connection, query: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in conn.execute(query, params)]


def _page(conn: Connection, query: Any, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    rows = _rows(conn, query, params)
    if not rows and params.get("offset"):
        # Página vacía por desplazamiento: se pide 1 fila desde el inicio solo para el total.
        probe = _rows(conn, query, params | {"limit": 1, "offset": 0})
        return [], int(probe[0]["_total"]) if probe else 0
    total = int(rows[0]["_total"]) if rows else 0
    for row in rows:
        row.pop("_total")
    return rows, total


def kpi_summary(conn: Connection, date_from: date | None, date_to: date | None) -> dict[str, Any]:
    data = dict(conn.execute(_KPIS, {"date_from": date_from, "date_to": date_to}).one()._mapping)
    data["average_order_value"] = (
        (data["revenue"] / data["orders"]).quantize(data["revenue"]) if data["orders"] else None
    )
    return data | {"date_from": date_from, "date_to": date_to}


def daily_sales(
    conn: Connection,
    *,
    date_from: date | None,
    date_to: date | None,
    category: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    params = {"date_from": date_from, "date_to": date_to, "category": category}
    return _page(conn, _DAILY_SALES, params | {"limit": limit, "offset": offset})


def sales_by_category(
    conn: Connection, date_from: date | None, date_to: date | None
) -> list[dict[str, Any]]:
    return _rows(conn, _BY_CATEGORY, {"date_from": date_from, "date_to": date_to})


def top_products(
    conn: Connection,
    *,
    metric: Literal["revenue", "units"],
    limit: int,
    date_from: date | None,
    date_to: date | None,
) -> list[dict[str, Any]]:
    params = {"date_from": date_from, "date_to": date_to, "limit": limit}
    return _rows(conn, _TOP_PRODUCTS[metric], params)


def pipeline_runs(
    conn: Connection, *, status: str | None, flow_name: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    params = {"status": status, "flow": flow_name, "limit": limit, "offset": offset}
    return _page(conn, _RUNS, params)


def pipeline_run(conn: Connection, run_id: uuid.UUID) -> dict[str, Any] | None:
    rows = _rows(conn, _RUN, {"id": run_id})
    if not rows:
        return None
    run = rows[0]
    run["tasks"] = _rows(conn, _RUN_TASKS, {"id": run_id})
    run["quality_failures"] = conn.execute(_RUN_QUALITY_FAILURES, {"id": run_id}).scalar_one()
    return run


def quality_summary(
    conn: Connection, since: datetime | None, layer: str | None
) -> list[dict[str, Any]]:
    return _rows(conn, _QUALITY_SUMMARY, {"since": since, "layer": layer})


def quality_results(
    conn: Connection, *, table_name: str | None, passed: bool | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    params = {"table_name": table_name, "passed": passed, "limit": limit, "offset": offset}
    return _page(conn, _QUALITY_RESULTS, params)


def batches(
    conn: Connection, *, source: str | None, status: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    params = {"source": source, "status": status, "limit": limit, "offset": offset}
    return _page(conn, _BATCHES, params)


def daily_volumes(conn: Connection, days: int) -> list[dict[str, Any]]:
    return _rows(conn, _VOLUMES, {"days": days})
