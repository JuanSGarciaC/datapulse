"""Gráficos Altair del dashboard (funciones puras: datos → especificación).

Reglas aplicadas (guía de visualización del proyecto):

* Un solo eje Y por gráfico; medidas de distinta escala van en gráficos distintos.
* Una serie → un solo color (azul), sin leyenda; ≥ 2 series → leyenda siempre.
* Colores categóricos en orden fijo (azul, naranja…), nunca generados.
* Colores de estado reservados (error/aviso) y siempre con etiqueta textual.
* Marcas finas: líneas de 2 px, barras con extremo redondeado de 4 px, tooltips.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Final

import altair as alt

SERIES: Final = ("#2a78d6", "#eb6834", "#1baf7a")  # paleta validada: azul, naranja, aqua
STATUS: Final = {"error": "#d03b3b", "warn": "#fab219", "ok": "#0ca30c"}
STATUS_LABEL: Final = {"error": "Error (cuarentena)", "warn": "Aviso"}
TEXT_MUTED: Final = "#52514e"
GRID: Final = "#e6e5e0"


Figure = alt.Chart | alt.LayerChart


def _base(values: list[dict[str, Any]]) -> alt.Chart:
    data = alt.Data(values=values)
    return alt.Chart(data).properties(width="container", height=280)


def _configure[C: (alt.Chart, alt.LayerChart)](chart: C) -> C:
    configured: C = (
        chart.configure_axis(
            labelColor=TEXT_MUTED,
            titleColor=TEXT_MUTED,
            gridColor=GRID,
            domain=False,
            tickColor=GRID,
            labelFontSize=12,
            titleFontSize=12,
            titleFontWeight="normal",
        )
        .configure_view(strokeWidth=0)
        .configure_legend(orient="top", labelColor=TEXT_MUTED, title=None, labelFontSize=12)
    )
    return configured


def _num(value: Any) -> float:
    return float(Decimal(str(value))) if value is not None else 0.0


def revenue_by_day(daily: list[dict[str, Any]]) -> Figure:
    totals: dict[str, float] = defaultdict(float)
    for row in daily:
        totals[row["order_date"]] += _num(row["revenue"])
    values = [{"day": d, "revenue": round(v, 2)} for d, v in sorted(totals.items())]
    hover = alt.selection_point(fields=["day"], nearest=True, on="pointerover", empty=False)
    base = _base(values).encode(
        # Una marca por día: sin esto Vega pone marcas cada pocas horas y repite la fecha.
        x=alt.X(
            "day:T",
            title=None,
            axis=alt.Axis(format="%d %b", labelAngle=0, tickCount={"interval": "day", "step": 1}),
        ),
        y=alt.Y("revenue:Q", title="Ingresos (USD)", axis=alt.Axis(format="~s")),
    )
    line = base.mark_line(strokeWidth=2, color=SERIES[0])
    points = (
        base.mark_point(filled=True, size=80, color=SERIES[0])
        .encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[
                alt.Tooltip("day:T", title="Día", format="%Y-%m-%d"),
                alt.Tooltip("revenue:Q", title="Ingresos", format=",.2f"),
            ],
        )
        .add_params(hover)
    )
    return _configure(alt.LayerChart(layer=[line, points]))


def revenue_by_category(rows: list[dict[str, Any]]) -> Figure:
    values = [
        {"category": r["category"], "revenue": _num(r["revenue"]), "orders": r["orders"]}
        for r in rows
    ]
    chart = (
        _base(values)
        .mark_bar(color=SERIES[0], cornerRadiusEnd=4, height={"band": 0.7})
        .encode(
            y=alt.Y("category:N", sort="-x", title=None),
            x=alt.X("revenue:Q", title="Ingresos (USD)", axis=alt.Axis(format="~s")),
            tooltip=[
                alt.Tooltip("category:N", title="Categoría"),
                alt.Tooltip("revenue:Q", title="Ingresos", format=",.2f"),
                alt.Tooltip("orders:Q", title="Pedidos"),
            ],
        )
    )
    return _configure(chart.properties(height=max(160, 26 * len(values))))


def quality_failures(summary: list[dict[str, Any]], *, top: int = 15) -> Figure:
    failing = sorted((s for s in summary if s["rows_failed"] > 0), key=lambda s: -s["rows_failed"])[
        :top
    ]
    values = [
        {
            "rule": f"{s['table_name']} · {s['rule_name']}"[:70],
            "rule_full": f"{s['table_name']} · {s['rule_name']}",
            "rows_failed": s["rows_failed"],
            "severity": STATUS_LABEL.get(s["severity"], s["severity"]),
            "layer": "antes de cargar" if s["layer"] == "pre_load" else "tras transformar",
        }
        for s in failing
    ]
    domain = [STATUS_LABEL["error"], STATUS_LABEL["warn"]]
    chart = (
        _base(values)
        .mark_bar(cornerRadiusEnd=4, height={"band": 0.7})
        .encode(
            y=alt.Y("rule:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320)),
            x=alt.X("rows_failed:Q", title="Filas que no cumplen la regla"),
            color=alt.Color(
                "severity:N",
                scale=alt.Scale(domain=domain, range=[STATUS["error"], STATUS["warn"]]),
                legend=alt.Legend(title=None),
            ),
            tooltip=[
                alt.Tooltip("rule_full:N", title="Regla"),
                alt.Tooltip("severity:N", title="Severidad"),
                alt.Tooltip("layer:N", title="Capa"),
                alt.Tooltip("rows_failed:Q", title="Filas"),
            ],
        )
    )
    return _configure(chart.properties(height=max(160, 26 * len(values))))


def rows_per_day(volumes: list[dict[str, Any]]) -> Figure:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"Cargadas": 0, "Rechazadas": 0})
    for v in volumes:
        totals[v["day"]]["Cargadas"] += v["rows_loaded"] or 0
        totals[v["day"]]["Rechazadas"] += v["rows_rejected"] or 0
    values = [
        {"day": d, "kind": k, "rows": n}
        for d, kinds in sorted(totals.items())
        for k, n in kinds.items()
    ]
    chart = (
        _base(values)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("day:O", title=None, axis=alt.Axis(labelAngle=0)),
            xOffset="kind:N",
            y=alt.Y("rows:Q", title="Filas"),
            color=alt.Color(
                "kind:N", scale=alt.Scale(domain=["Cargadas", "Rechazadas"], range=list(SERIES[:2]))
            ),
            tooltip=[
                alt.Tooltip("day:O", title="Día"),
                alt.Tooltip("kind:N", title="Tipo"),
                alt.Tooltip("rows:Q", title="Filas", format=","),
            ],
        )
    )
    return _configure(chart)


def duration_by_flow(volumes: list[dict[str, Any]]) -> Figure:
    per_flow: dict[str, list[float]] = defaultdict(list)
    for v in volumes:
        if v["avg_duration_s"] is not None:
            per_flow[v["flow_name"]].append(v["avg_duration_s"])
    values = [
        {"flow": f, "seconds": round(sum(d) / len(d), 2), "max": round(max(d), 2)}
        for f, d in sorted(per_flow.items())
    ]
    chart = (
        _base(values)
        .mark_bar(color=SERIES[0], cornerRadiusEnd=4, height={"band": 0.7})
        .encode(
            y=alt.Y("flow:N", sort="-x", title=None),
            x=alt.X("seconds:Q", title="Duración media (s)"),
            tooltip=[
                alt.Tooltip("flow:N", title="Flujo"),
                alt.Tooltip("seconds:Q", title="Media (s)"),
                alt.Tooltip("max:Q", title="Máxima (s)"),
            ],
        )
    )
    return _configure(chart.properties(height=max(120, 34 * len(values))))
