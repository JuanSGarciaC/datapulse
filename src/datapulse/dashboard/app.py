"""Dashboard de DataPulse (Streamlit). Ejecutar: ``streamlit run`` sobre este archivo
o ``python -m datapulse.dashboard``.

Tres vistas: negocio (indicadores), calidad de datos y pipelines (volúmenes,
tiempos y errores). Todo llega por la API; el dashboard no toca la base de datos.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import streamlit as st

from datapulse.dashboard import charts
from datapulse.dashboard.client import ApiClient, ApiError
from datapulse.dashboard.formatting import money as _money
from datapulse.dashboard.formatting import money_compact as _money_compact

STATUS_BADGE = {
    "success": "✅ éxito",
    "partial": "⚠️ parcial",
    "failed": "❌ fallo",
    "running": "⏳ en curso",
}


def _client() -> Any:
    # Las pruebas inyectan un cliente falso en session_state antes de ejecutar la página.
    if "_client" not in st.session_state:
        st.session_state["_client"] = ApiClient.from_settings()
    return st.session_state["_client"]


def _business(client: Any, date_from: date | None, date_to: date | None) -> None:
    kpis = client.kpis(date_from, date_to)
    cols = st.columns(5)
    cols[0].metric(
        "Ingresos", _money_compact(kpis["revenue"]), help=_money(kpis["revenue"]), border=True
    )
    cols[1].metric("Pedidos", f"{kpis['orders']:,}", border=True)
    cols[2].metric(
        "Ticket medio",
        _money_compact(kpis["average_order_value"]),
        help=_money(kpis["average_order_value"]),
        border=True,
    )
    cols[3].metric("Clientes activos", f"{kpis['active_customers']:,}", border=True)
    cols[4].metric(
        "Cancelados o devueltos", f"{kpis['cancelled_or_returned_orders']:,}", border=True
    )

    daily = client.daily_sales(date_from, date_to)
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Ingresos por día")
        if daily:
            st.altair_chart(charts.revenue_by_day(daily), width="stretch", theme=None)
        else:
            st.info("Sin ventas en el rango seleccionado.")
    with right:
        st.subheader("Ingresos por categoría")
        by_category = client.sales_by_category(date_from, date_to)
        if by_category:
            st.altair_chart(charts.revenue_by_category(by_category), width="stretch", theme=None)

    st.subheader("Productos más vendidos")
    st.dataframe(
        client.top_products(10, date_from, date_to),
        hide_index=True,
        width="stretch",
        column_config={
            "product_id": "Producto",
            "title": "Nombre",
            "category": "Categoría",
            "units": st.column_config.NumberColumn("Unidades"),
            "revenue": st.column_config.NumberColumn("Ingresos", format="%.2f"),
        },
    )
    with st.expander("Tabla de ventas diarias (vista accesible)"):
        st.dataframe(daily, hide_index=True, width="stretch")


def _quality(client: Any) -> None:
    summary = client.quality_summary()
    failing = [s for s in summary if s["rows_failed"] > 0]
    errors = sum(s["rows_failed"] for s in failing if s["severity"] == "error")
    warns = sum(s["rows_failed"] for s in failing if s["severity"] == "warn")
    cols = st.columns(3)
    cols[0].metric("Reglas evaluadas", f"{len(summary):,}", border=True)
    cols[1].metric("❌ Filas con error (cuarentena)", f"{errors:,}", border=True)
    cols[2].metric("⚠️ Filas con aviso", f"{warns:,}", border=True)

    st.subheader("Reglas con fallos")
    if failing:
        st.altair_chart(charts.quality_failures(summary), width="stretch", theme=None)
        st.caption(
            "Error: la fila va a cuarentena antes de cargarse. Aviso: se carga, pero se "
            "señala (p. ej. pedidos de productos o clientes que no están en la dimensión)."
        )
    else:
        st.success("Todas las reglas pasan.")
    st.subheader("Últimos resultados fallidos")
    st.dataframe(
        client.failed_quality_results(),
        hide_index=True,
        width="stretch",
        column_order=(
            "checked_at",
            "layer",
            "table_name",
            "rule_name",
            "severity",
            "rows_failed",
            "details",
        ),
    )


def _pipelines(client: Any, days: int) -> None:
    volumes = client.volumes(days)
    runs = client.pipeline_runs(100)
    failed = [r for r in runs if r["status"] == "failed"]
    cols = st.columns(4)
    cols[0].metric("Ejecuciones", f"{len(runs):,}", border=True)
    cols[1].metric("❌ Con fallo", f"{len(failed):,}", border=True)
    cols[2].metric(
        "Filas cargadas", f"{sum(v['rows_loaded'] or 0 for v in volumes):,}", border=True
    )
    cols[3].metric(
        "Filas rechazadas", f"{sum(v['rows_rejected'] or 0 for v in volumes):,}", border=True
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Volumen por día")
        if volumes:
            st.altair_chart(charts.rows_per_day(volumes), width="stretch", theme=None)
    with right:
        st.subheader("Duración media por flujo")
        if volumes:
            st.altair_chart(charts.duration_by_flow(volumes), width="stretch", theme=None)

    st.subheader("Ejecuciones recientes")
    table = [
        {
            "inicio": r["started_at"],
            "flujo": r["flow_name"],
            "estado": STATUS_BADGE.get(r["status"], r["status"]),
            "duración (s)": round(r["duration_s"], 1) if r["duration_s"] is not None else None,
            "leídas": r["rows_read"],
            "cargadas": r["rows_loaded"],
            "rechazadas": r["rows_rejected"],
            "error": r["error_message"],
            "run_id": r["run_id"],
        }
        for r in runs
    ]
    st.dataframe(table, hide_index=True, width="stretch")

    if runs:
        chosen = st.selectbox(
            "Detalle de una ejecución",
            options=[r["run_id"] for r in runs],
            format_func=lambda rid: next(
                f"{r['flow_name']} · {STATUS_BADGE.get(r['status'], r['status'])} · "
                f"{r['started_at'][:19]}"
                for r in runs
                if r["run_id"] == rid
            ),
        )
        detail = client.run_detail(chosen)
        st.dataframe(detail["tasks"], hide_index=True, width="stretch")
        if detail["error_message"]:
            st.error(f"{detail['error_type']}: {detail['error_message']}")


def main() -> None:
    st.set_page_config(page_title="DataPulse", page_icon="📊", layout="wide")
    st.title("DataPulse")
    client = _client()
    ready = client.ready()
    if ready.get("status") != "ok":
        st.warning("La API o la base de datos no están disponibles. Los datos pueden faltar.")

    # Filtros en una sola fila, encima de todo.
    f1, f2, f3 = st.columns([2, 2, 1])
    date_from = f1.date_input("Desde", value=date(2026, 1, 1))
    date_to = f2.date_input("Hasta", value=date(2026, 1, 3))
    days = f3.number_input("Ventana operativa (días)", min_value=1, max_value=366, value=30)
    if isinstance(date_from, date) and isinstance(date_to, date) and date_from > date_to:
        st.error("La fecha «Desde» no puede ser posterior a «Hasta».")
        return

    business, quality, pipelines = st.tabs(["Negocio", "Calidad de datos", "Pipelines"])
    with business:
        _guarded(_business, client, date_from, date_to)
    with quality:
        _guarded(_quality, client)
    with pipelines:
        _guarded(_pipelines, client, int(days))


def _guarded(render: Callable[..., None], *args: Any) -> None:
    """Un fallo de la API se muestra en su pestaña sin tumbar las demás."""
    try:
        render(*args)
    except ApiError as exc:
        st.error(str(exc))


# Streamlit ejecuta el archivo como script; las pruebas lo cargan igual (AppTest).
main()
