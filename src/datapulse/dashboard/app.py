"""Dashboard de DataPulse (Streamlit). Ejecutar: ``streamlit run`` sobre este archivo
o ``python -m datapulse.dashboard``.

Arriba, el mapa del pipeline con el estado de la última ejecución de cada etapa. Debajo,
tres vistas: negocio (indicadores), calidad de datos y pipelines (volúmenes, tiempos,
errores y lotes de ingesta). Todo llega por la API; el dashboard no toca la base de datos.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import streamlit as st

from datapulse.dashboard import charts, pipeline_map, theme
from datapulse.dashboard.client import ApiClient, ApiError, SourceChaos
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
# Una ejecución «en curso» más vieja que esto es una fila huérfana (el worker murió): no debe
# mantener el refresco encendido para siempre.
RUNNING_MAX_AGE = timedelta(minutes=15)
# Validador en vivo: fuente → (etiqueta, extensión, ejemplo con defectos reales de data/samples).
CONTRACT_SOURCES = {
    "customers": ("Clientes (CSV)", "csv", "clientes_con_defectos.csv"),
    "orders": ("Pedidos (JSON)", "json", "pedidos_con_defectos.json"),
}
EXAMPLES = Path(__file__).with_name("examples")
MAX_UPLOAD_BYTES = 1_000_000  # el mismo tope que aplica la API
ORDER_ID = re.compile(r"O\d{6}")


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
        # La API no filtra los resultados por capa: se filtra aquí para que la tabla
        # acompañe a las tarjetas y al gráfico.
        [r for r in client.failed_quality_results() if layer in {None, r["layer"]}],
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
    _quarantine_tray(client)


def _quarantine_tray(client: Any) -> None:
    st.subheader("Bandeja de cuarentena")
    st.caption(
        "Cada fila apartada antes de cargarse, con su motivo y el archivo del que vino. Los "
        "nombres no salen de la base de datos y el email llega enmascarado."
    )
    rows = [
        {
            "clave": q["record_key"],
            "campo": e["field"],
            "motivo": e["message"],
            # Valor que falló, si es un campo de primer nivel (ya enmascarado si es el email).
            "valor": _shown(q["payload_masked"], e["field"]),
            "archivo": q["source_ref"],
            "fila": q["row_number"],
            "fuente": q["source_name"],
            "regla": e["rule"],
            "lote": q["content_sha256"][:12],
            "recibida": q["quarantined_at"][:16].replace("T", " "),
        }
        for q in client.quarantine()
        for e in q["errors"]
    ]
    if not rows:
        st.success("No hay filas en cuarentena.")
        return
    c1, c2, c3 = st.columns([1, 1, 1])
    source = c1.selectbox(
        "Fuente", [None, *sorted({r["fuente"] for r in rows})],
        format_func=lambda v: v or "Todas", key="q_source",
    )  # fmt: skip
    field = c2.selectbox(
        "Campo", [None, *sorted({r["campo"] or "" for r in rows})],
        format_func=lambda v: v or "Todos", key="q_field",
    )  # fmt: skip
    key = c3.text_input("Buscar clave", key="q_key", max_chars=40).strip()
    shown = [
        r
        for r in rows
        if source in {None, r["fuente"]}
        and field in {None, r["campo"] or ""}
        and (not key or key.lower() in str(r["clave"] or "").lower())
    ]
    files = len({r["archivo"] for r in rows})
    st.caption(f"{len(shown)} de {len(rows)} errores, en {files} archivos.")
    st.dataframe(shown, hide_index=True, width="stretch")


def _shown(payload: dict[str, Any], field: str | None) -> str:
    value = payload.get(field) if field else None
    if field and field in payload:
        return "(vacío)" if value is None else str(value)[:80]
    return "—"


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


def _contracts(client: Any) -> None:
    st.caption(
        "Sube un archivo y mira qué haría la ingesta con él: las mismas reglas que el pipeline, "
        "sin guardar nada."
    )
    source = st.radio(
        "Fuente",
        list(CONTRACT_SOURCES),
        format_func=lambda s: CONTRACT_SOURCES[s][0],
        horizontal=True,
        key="contract_source",
    )
    _, ext, example = CONTRACT_SOURCES[source]
    upload = st.file_uploader(
        f"Archivo .{ext} (máximo 1 MB)", type=[ext], key=f"contract_upload_{source}"
    )
    if st.button("Cargar ejemplo con defectos", key=f"contract_example_{source}"):
        st.session_state["_contract_example"] = source
    if upload is not None:
        data, name = upload.getvalue(), upload.name
    elif st.session_state.get("_contract_example") == source:
        data, name = (EXAMPLES / example).read_bytes(), example
    else:
        st.info("Sube un archivo o carga el ejemplo: son filas reales de las muestras.")
        return
    if len(data) > MAX_UPLOAD_BYTES:
        st.error("El archivo supera 1 MB.")
        return
    _contract_result(client.validate_file(source, data), name)


def _contract_result(result: dict[str, Any], name: str) -> None:
    cols = st.columns(4)
    cols[0].metric("Filas leídas", f"{result['rows_read']:,}", border=True)
    cols[1].metric("Se cargarían", f"{result['rows_valid']:,}", border=True)
    cols[2].metric("A cuarentena", f"{result['rows_quarantined']:,}", border=True)
    cols[3].metric("Duplicadas descartadas", f"{result['duplicates_dropped']:,}", border=True)
    if result["rejected_reason"]:
        st.error(f"{name}: el lote se rechazaría entero. {result['rejected_reason']}.")
    elif result["rows_quarantined"]:
        st.warning(
            f"{name}: {result['rows_quarantined']} filas irían a cuarentena con su motivo "
            f"(tasa de error {result['error_rate']:.1%}); el resto se cargaría."
        )
    else:
        st.success(f"{name}: todas las filas cumplen el contrato.")
    if result["quarantined"]:
        st.subheader("Filas que irían a cuarentena")
        st.dataframe(
            [
                {
                    "fila": q["row_number"],
                    "clave": q["record_key"],
                    "campo": e["field"],
                    "motivo": e["message"],
                    "regla": e["rule"],
                }
                for q in result["quarantined"]
                for e in q["errors"]
            ],
            hide_index=True,
            width="stretch",
        )
    if result["failed_rules"]:
        with st.expander("Reglas que fallaron"):
            st.dataframe(result["failed_rules"], hide_index=True, width="stretch")


def _lineage(client: Any) -> None:
    st.caption(
        "Escribe un pedido y sigue su rastro: del indicador a cada versión recibida, su lote "
        "con huella SHA-256, el archivo y la ejecución que lo cargó."
    )
    left, right = st.columns([1, 2])
    order_id = left.text_input("Pedido", value="O000065", max_chars=7, key="lineage_id")
    order_id = order_id.strip().upper()
    right.caption(
        "Para probar: O000065 llegó dos veces · O000231 fue a cuarentena · O000001 es un "
        "pedido normal."
    )
    if not ORDER_ID.fullmatch(order_id):
        st.error("Escribe una O seguida de 6 dígitos, por ejemplo O000065.")
        return
    try:
        lineage = client.order_lineage(order_id)
    except ApiError as exc:
        if "404" not in str(exc):
            raise
        st.info(f"No hay rastro de {order_id}: no llegó en ningún archivo.")
        return
    _lineage_facts(lineage)
    _lineage_versions(lineage)
    if lineage["quarantined"]:
        with st.container(border=True):
            st.markdown("**En cuarentena** · no se cargó")
            for q in lineage["quarantined"]:
                reasons = "; ".join(f"{e['field']}: {e['message']}" for e in q["errors"])
                st.markdown(
                    f"`{q['source_ref']}`, fila {q['row_number']} · lote "
                    f"`{q['content_sha256'][:12]}` · {reasons}"
                )


def _lineage_facts(lineage: dict[str, Any]) -> None:
    lines = lineage["fact_lines"]
    with st.container(border=True):
        st.markdown("**1 · En el indicador** · `marts.fct_order_items`")
        if not lines:
            st.markdown("No llegó a marts.")
            return
        amount = sum(float(line["line_amount"]) for line in lines)
        counts = lines[0]["is_revenue"]
        verdict = (
            "cuenta como ingreso" if counts else f"no cuenta como ingreso ({lines[0]['status']})"
        )
        st.markdown(f"{len(lines)} líneas · importe {_money(amount)} · {verdict}")
        st.dataframe(lines, hide_index=True, width="stretch")


def _lineage_versions(lineage: dict[str, Any]) -> None:
    versions = lineage["versions"]
    if not versions:
        return
    st.caption("↑ viene de")
    with st.container(border=True):
        st.markdown(
            f"**2 · Versiones recibidas** · `raw.orders` · {len(versions)} "
            + ("versión" if len(versions) == 1 else "versiones: gana la más reciente")
        )
        st.dataframe(
            [
                {
                    "versión": "vigente" if v["is_current"] else "superada",
                    "status": v["status"],
                    "actualizado": v["updated_at"][:16].replace("T", " "),
                    "archivo": v["source_ref"],
                    "fila": v["row_number"],
                    "lote (sha256)": v["content_sha256"][:12],
                }
                for v in versions
            ],
            hide_index=True,
            width="stretch",
        )
    current = next(v for v in versions if v["is_current"])
    st.caption("↑ viene de")
    with st.container(border=True):
        st.markdown("**3 · Lote y archivo** de la versión vigente")
        st.markdown(
            f"`{current['source_ref']}`, fila {current['row_number']} · "
            f"sha256 `{current['content_sha256']}`"
        )
    st.caption("↑ lo cargó")
    with st.container(border=True):
        st.markdown("**4 · Ejecución**")
        started = (current["run_started_at"] or "")[:16].replace("T", " ")
        st.markdown(
            f"flujo `{current['flow_name']}` · disparo {current['trigger']} · {started} · "
            f"run_id `{current['run_id']}`"
        )


# El intervalo se decide con lo visto en la pasada anterior: Streamlit lo fija al decorar.
@st.fragment(run_every=REFRESH_S if st.session_state.get("_live") else None)
def _overview(client: Any) -> None:
    """Mapa del pipeline y, si hay clave de administración, disparo de flujos."""
    with st.container(key="dp-map"):
        st.subheader("Mapa del pipeline")
        if client.can_trigger:
            chaos = _chaos()
            cols = st.columns([1, 1, 1, 1] if chaos.available else [1, 1, 2])
            for col, (name, label) in zip(cols, TRIGGERS.items(), strict=False):
                if col.button(label, key=f"trigger_{name}", width="stretch"):
                    _trigger(client, name)
            if chaos.available:
                _chaos_buttons(client, chaos, cols[2], cols[3])
        try:
            runs = client.pipeline_runs(100)
            svg = pipeline_map.render(
                runs, client.quality_summary(), client.batches(100), client.freshness()
            )
            # st.html descarta el SVG (su saneador solo admite HTML); st.markdown lo conserva.
            st.markdown(svg, unsafe_allow_html=True)
        except ApiError as exc:
            st.error(str(exc))
            return
    recent = datetime.now(UTC) - RUNNING_MAX_AGE
    running = any(
        r["status"] == "running" and datetime.fromisoformat(r["started_at"]) > recent for r in runs
    )
    live = running or time.time() < st.session_state.get("_watch_until", 0)
    if live != st.session_state.get("_live", False):
        # Rehace la página entera: activa o apaga el refresco y actualiza las pestañas.
        st.session_state["_live"] = live
        st.rerun()


def _chaos() -> Any:
    if "_chaos" not in st.session_state:
        st.session_state["_chaos"] = SourceChaos.from_settings()
    return st.session_state["_chaos"]


def _chaos_buttons(client: Any, chaos: Any, break_col: Any, restore_col: Any) -> None:
    """Demo de resiliencia: la fuente falla y el pipeline se recupera solo."""
    if break_col.button(
        "Provocar fallos y ejecutar productos",
        key="chaos_break",
        width="stretch",
        help=f"La fuente simulada devolverá {SourceChaos.FAILURES} errores 503 seguidos.",
    ):
        try:
            chaos.break_source()
        except ApiError as exc:
            st.error(str(exc))
        else:
            # Se guarda en la sesión: al disparar, la página se rehace para el refresco en vivo.
            st.session_state["_chaos_note"] = True
            _trigger(client, "products")
    if restore_col.button("Restaurar fuente", key="chaos_restore", width="stretch"):
        try:
            chaos.restore()
        except ApiError as exc:
            st.error(str(exc))
        else:
            st.session_state["_chaos_note"] = False
            st.toast("La fuente simulada vuelve a responder sin fallos.")
    if st.session_state.get("_chaos_note"):
        st.info(
            f"La fuente fallará {SourceChaos.FAILURES} veces seguidas. El cliente reintenta con "
            "espera creciente; al agotarse, Prefect reintenta la tarea y la segunda vez entra. "
            "Míralo en Pipelines → Detalle de una ejecución (columna attempt)."
        )


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

    business, quality, pipelines, contracts, lineage = st.tabs(
        ["Negocio", "Calidad de datos", "Pipelines", "Probar contratos", "Linaje"]
    )
    with business:
        _guarded(_business, client, date_from, date_to)
    with quality:
        _guarded(_quality, client)
    with pipelines:
        _guarded(_pipelines, client, int(days))
    with contracts:
        _guarded(_contracts, client)
    with lineage:
        _guarded(_lineage, client)


def _guarded(render: Callable[..., None], *args: Any) -> None:
    """Un fallo de la API se muestra en su pestaña sin tumbar las demás."""
    try:
        render(*args)
    except ApiError as exc:
        st.error(str(exc))


# Streamlit ejecuta el archivo como script; las pruebas lo cargan igual (AppTest).
main()
