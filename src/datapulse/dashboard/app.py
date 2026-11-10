"""Dashboard de DataPulse (Streamlit). Ejecutar: ``streamlit run`` sobre este archivo
o ``python -m datapulse.dashboard``.

Arriba, el mapa del pipeline con el estado de la última ejecución de cada etapa. Debajo,
tres vistas: negocio (indicadores), calidad de datos y pipelines (volúmenes, tiempos,
errores y lotes de ingesta). Todo llega por la API; el dashboard no toca la base de datos.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any

import streamlit as st

from datapulse.dashboard import charts, pipeline_map, theme
from datapulse.dashboard.client import ApiClient, ApiError
from datapulse.dashboard.formatting import STATUS_LABEL
from datapulse.dashboard.formatting import money as _money
from datapulse.dashboard.formatting import money_compact as _money_compact

# Nombre público del despliegue en la API → texto del botón.
TRIGGERS = {"daily": "Ejecutar flujo diario", "products": "Ejecutar ingesta de productos"}
RUN_STATUSES = ("success", "partial", "failed", "running")
LAYERS = {None: "Todas", "pre_load": "Antes de cargar", "post_transform": "Tras transformar"}
METRICS = {"revenue": "Ingresos", "units": "Unidades"}
# shortcut: tras disparar se refresca durante una ventana fija, no hasta ver terminar esa
# ejecución concreta; cambiarlo si los flujos pasan a durar más de un minuto.
WATCH_S = 60
REFRESH_S = 5


def _client() -> Any:
    # Las pruebas inyectan un cliente falso en session_state antes de ejecutar la página.
    if "_client" not in st.session_state:
        st.session_state["_client"] = ApiClient.from_settings()
    return st.session_state["_client"]


def _business(client: Any, date_from: date | None, date_to: date | None) -> None:
    kpis = client.kpis(date_from, date_to)
    cols = st.columns(6)
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
    cols[3].metric("Unidades", f"{kpis['units']:,}", border=True)
    cols[4].metric("Clientes activos", f"{kpis['active_customers']:,}", border=True)
    cols[5].metric(
        "Cancelaciones",
        f"{kpis['cancelled_or_returned_orders']:,}",
        help="Pedidos cancelados o devueltos",
        border=True,
    )

    by_category = client.sales_by_category(date_from, date_to)
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Ingresos por día")
        category = st.selectbox(
            "Categoría",
            [None, *(c["category"] for c in by_category)],
            format_func=lambda c: c or "Todas",
            key="sales_category",
        )
        daily = client.daily_sales(date_from, date_to, category)
        if daily:
            st.altair_chart(charts.revenue_by_day(daily), width="stretch", theme=None)
        else:
            st.info("Sin ventas en el rango seleccionado.")
    with right:
        st.subheader("Ingresos por categoría")
        if by_category:
            st.altair_chart(charts.revenue_by_category(by_category), width="stretch", theme=None)

    st.subheader("Productos más vendidos")
    metric = st.radio(
        "Ordenar por",
        list(METRICS),
        format_func=METRICS.__getitem__,
        horizontal=True,
        key="top_metric",
    )
    st.dataframe(
        client.top_products(10, date_from, date_to, metric),
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
    layer = st.radio(
        "Capa", list(LAYERS), format_func=LAYERS.__getitem__, horizontal=True, key="quality_layer"
    )
    summary = client.quality_summary(layer)
    failing = [s for s in summary if s["rows_failed"] > 0]
    errors = sum(s["rows_failed"] for s in failing if s["severity"] == "error")
    warns = sum(s["rows_failed"] for s in failing if s["severity"] == "warn")
    cols = st.columns(3)
    cols[0].metric("Reglas evaluadas", f"{len(summary):,}", border=True)
    cols[1].metric("Filas con error (cuarentena)", f"{errors:,}", border=True)
    cols[2].metric("Filas con aviso", f"{warns:,}", border=True)

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
    cols[1].metric("Con fallo", f"{len(failed):,}", border=True)
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
    status = st.selectbox(
        "Estado",
        [None, *RUN_STATUSES],
        format_func=lambda v: STATUS_LABEL[v] if v else "Todos",
        key="runs_status",
    )
    shown = runs if status is None else client.pipeline_runs(100, status)
    table = [
        {
            "inicio": r["started_at"],
            "flujo": r["flow_name"],
            "estado": STATUS_LABEL.get(r["status"], r["status"]),
            "duración (s)": round(r["duration_s"], 1) if r["duration_s"] is not None else None,
            "leídas": r["rows_read"],
            "cargadas": r["rows_loaded"],
            "rechazadas": r["rows_rejected"],
            "error": r["error_message"],
            "run_id": r["run_id"],
        }
        for r in shown
    ]
    st.dataframe(table, hide_index=True, width="stretch")

    if shown:
        chosen = st.selectbox(
            "Detalle de una ejecución",
            options=[r["run_id"] for r in shown],
            format_func=lambda rid: next(
                f"{r['flow_name']} · {STATUS_LABEL.get(r['status'], r['status'])} · "
                f"{r['started_at'][:19]}"
                for r in shown
                if r["run_id"] == rid
            ),
        )
        detail = client.run_detail(chosen)
        st.caption(
            f"Disparo: {detail['trigger']} · Fallos de calidad: {detail['quality_failures']} · "
            f"Ejecución en Prefect: {detail.get('orchestrator_run_id') or 'sin orquestador'}"
        )
        st.dataframe(detail["tasks"], hide_index=True, width="stretch")
        if detail["params"]:
            with st.expander("Parámetros de la ejecución"):
                st.json(detail["params"])
        if detail["error_message"]:
            st.error(f"{detail['error_type']}: {detail['error_message']}")
    else:
        st.info("Sin ejecuciones con ese estado.")

    st.subheader("Lotes de ingesta")
    st.caption(
        "Cada archivo o página cargada es un lote con su huella (SHA-256). Un archivo "
        "reenviado sin cambios no crea un lote nuevo: así la ingesta es idempotente."
    )
    st.dataframe(
        [
            {
                "creado": b["created_at"],
                "fuente": b["source_name"],
                "referencia": b["source_ref"],
                "estado": STATUS_LABEL.get(b["status"], b["status"]),
                "filas": b["row_count"],
                "rechazadas": b["rejected_count"],
                "huella": b["content_sha256"][:12],
                "run_id": b["run_id"],
            }
            for b in client.batches(100)
        ],
        hide_index=True,
        width="stretch",
    )


# El intervalo se decide con lo visto en la pasada anterior: Streamlit lo fija al decorar.
@st.fragment(run_every=REFRESH_S if st.session_state.get("_live") else None)
def _overview(client: Any) -> None:
    """Mapa del pipeline y, si hay clave de administración, disparo de flujos."""
    with st.container(key="dp-map"):
        st.subheader("Mapa del pipeline")
        if client.can_trigger:
            for col, (name, label) in zip(st.columns([1, 1, 3]), TRIGGERS.items(), strict=False):
                if col.button(label, key=f"trigger_{name}", width="stretch"):
                    _trigger(client, name)
        try:
            runs = client.pipeline_runs(100)
            svg = pipeline_map.render(runs, client.quality_summary(), client.batches(100))
            # st.html descarta el SVG (su saneador solo admite HTML); st.markdown lo conserva.
            st.markdown(svg, unsafe_allow_html=True)
        except ApiError as exc:
            st.error(str(exc))
            return
    live = any(r["status"] == "running" for r in runs) or time.time() < st.session_state.get(
        "_watch_until", 0
    )
    if live != st.session_state.get("_live", False):
        # Rehace la página entera: activa o apaga el refresco y actualiza las pestañas.
        st.session_state["_live"] = live
        st.rerun()


def _trigger(client: Any, name: str) -> None:
    try:
        run = client.trigger(name)
    except ApiError as exc:
        st.error(str(exc))
        return
    st.session_state["_watch_until"] = time.time() + WATCH_S
    st.toast(f"Ejecución creada en Prefect ({run['state']}). El mapa se actualiza solo.")


def main() -> None:
    st.set_page_config(page_title="DataPulse", layout="wide")
    theme.inject()
    st.title("DataPulse")
    client = _client()
    ready = client.ready()
    if ready.get("status") == "ok":
        st.html('<span class="dp-pill ok">API y base de datos disponibles</span>')
    else:
        st.warning("La API o la base de datos no están disponibles. Los datos pueden faltar.")
    _overview(client)

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
