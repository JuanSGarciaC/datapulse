"""Mapa del pipeline: datos de la API → SVG (función pura, sin Streamlit).

Fuentes → validación → raw → dbt → marts. Cada nodo lleva el estado de su última
ejecución. Una arista solo fluye si su etapa de origen tiene datos, y un nodo late mientras
su flujo está en curso: el movimiento informa, no decora.

Todo texto que viene de la API pasa por ``html.escape``: el resultado se inserta como HTML.

``flow_model`` resume los mismos datos en números (sin texto de la API) para la capa de
partículas del navegador (``dashboard/flow.py``): el SVG es la verdad y se lee sin JavaScript.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
from typing import Any, Final

from datapulse.dashboard.formatting import STATUS_LABEL

W: Final = 190
H: Final = 72
VIEW: Final = (1200, 340)
# Bandeja de cuarentena, bajo el nodo de validación: adonde van las filas con error.
BIN: Final = (300, 236, 130, 40)


@dataclass(frozen=True)
class Node:
    key: str
    title: str
    x: int
    y: int
    state: str  # success | partial | failed | running | idle
    line: str
    detail: str


def _latest(runs: list[dict[str, Any]], flow: str) -> dict[str, Any] | None:
    mine = [r for r in runs if r["flow_name"] == flow]
    return max(mine, key=lambda r: r["started_at"]) if mine else None


def _flow_node(key: str, title: str, x: int, y: int, run: dict[str, Any] | None) -> Node:
    if run is None:
        return Node(
            key, title, x, y, "idle", "sin ejecuciones", "Este flujo aún no se ha ejecutado."
        )
    seconds = f" · {run['duration_s']:.1f} s" if run["duration_s"] is not None else ""
    detail = (
        f"{title}: {run['rows_read']:,} leídas, {run['rows_loaded']:,} cargadas, "
        f"{run['rows_rejected']:,} rechazadas · disparo: {run['trigger']} · "
        f"inicio {run['started_at'][11:19]} UTC"
    )
    if run["error_message"]:
        detail += f" · {run['error_message']}"[:120]  # el mensaje ya incluye el tipo
    # dbt no cuenta filas: su nodo muestra solo la duración.
    moved = run["rows_read"] or run["rows_loaded"]
    line = f"{run['rows_loaded']:,} filas{seconds}" if moved else seconds.removeprefix(" · ")
    return Node(key, title, x, y, run["status"], line or "sin duración", detail)


def _quality_node(
    key: str, title: str, x: int, y: int, quality: list[dict[str, Any]], *, layer: str
) -> Node:
    rules = [q for q in quality if q["layer"] == layer]
    if not rules:
        return Node(key, title, x, y, "idle", "sin reglas evaluadas", "Aún no hay resultados.")
    errors = sum(q["rows_failed"] for q in rules if q["severity"] == "error")
    warns = sum(q["rows_failed"] for q in rules if q["severity"] == "warn")
    failing = sum(1 for q in rules if q["rows_failed"] > 0)
    detail = (
        f"{title}: {len(rules)} reglas, {failing} con fallos · "
        f"{errors:,} filas con error (cuarentena) · {warns:,} con aviso"
    )
    line = f"{len(rules)} reglas · {errors + warns:,} filas señaladas"
    return Node(key, title, x, y, "partial" if failing else "success", line, detail)


def _raw_node(x: int, y: int, batches: list[dict[str, Any]]) -> Node:
    if not batches:
        return Node("raw", "raw", x, y, "idle", "sin lotes", "Aún no se ha cargado ningún lote.")
    rows = sum(b["row_count"] for b in batches)
    rejected = sum(b["rejected_count"] for b in batches)
    bad = sum(1 for b in batches if b["status"] in {"failed", "rejected"})
    detail = (
        f"raw: {len(batches)} lotes, {rows:,} filas, {rejected:,} en cuarentena · "
        f"{bad} lotes fallidos o rechazados"
    )
    state = "partial" if bad or rejected else "success"
    return Node("raw", "raw", x, y, state, f"{len(batches)} lotes · {rows:,} filas", detail)


def nodes(
    runs: list[dict[str, Any]], quality: list[dict[str, Any]], batches: list[dict[str, Any]]
) -> list[Node]:
    mid = 114
    return [
        _flow_node("customers", "Clientes (CSV)", 20, 20, _latest(runs, "ingest_customers")),
        _flow_node("orders", "Pedidos (JSON)", 20, mid, _latest(runs, "ingest_orders")),
        _flow_node("products", "Productos (API)", 20, 208, _latest(runs, "ingest_products")),
        _quality_node("validation", "Validación", 270, mid, quality, layer="pre_load"),
        _raw_node(510, mid, batches),
        _flow_node("dbt", "dbt (staging y marts)", 750, mid, _latest(runs, "transform")),
        _quality_node("marts", "Pruebas de marts", 990, mid, quality, layer="post_transform"),
    ]


EDGES: Final = (
    ("customers", "validation"),
    ("orders", "validation"),
    ("products", "validation"),
    ("validation", "raw"),
    ("raw", "dbt"),
    ("dbt", "marts"),
)

_CSS: Final = """
.dp-map{width:100%;height:auto;display:block;font-family:inherit}
.dp-map .box{fill:#111726;stroke:rgba(255,255,255,.10);stroke-width:1;
  transition:stroke .2s,fill .2s}
.dp-map .node{cursor:default;outline:none}
.dp-map .node:hover .box,.dp-map .node:focus .box{fill:#172035;stroke:var(--c)}
.dp-map .title{fill:#e6eaf2;font-size:14px;font-weight:600}
.dp-map .line{fill:#9aa4b8;font-size:12px}
.dp-map .state{fill:var(--c);font-size:11px;font-weight:600;letter-spacing:.04em;
  text-transform:uppercase}
.dp-map .dot{fill:var(--c)}
.dp-map .detail,.dp-map .hint{fill:#c5cde0;font-size:13px;text-anchor:middle;transition:opacity .2s}
.dp-map .detail{opacity:0}
.dp-map .node:hover .detail,.dp-map .node:focus .detail{opacity:1}
.dp-map:has(.node:hover) .hint,.dp-map:has(.node:focus) .hint{opacity:0}
.dp-map .edge{fill:none;stroke:rgba(255,255,255,.12);stroke-width:2;transition:stroke .2s}
.dp-map .edge.flowing{stroke:#5b9dff;stroke-opacity:.8;stroke-dasharray:6 10;
  animation:dp-flow 1.6s linear infinite}
.dp-map .is-success{--c:#3ddc84}.dp-map .is-partial{--c:#ffc247}.dp-map .is-failed{--c:#ff5d5d}
.dp-map .is-running{--c:#5b9dff}.dp-map .is-idle{--c:#8a93a8}
.dp-map .is-running .box{stroke:var(--c);animation:dp-pulse 1.4s ease-in-out infinite}
.dp-map .qedge{fill:none;stroke:rgba(255,255,255,.10);stroke-width:1.5;stroke-dasharray:3 5}
.dp-map .qedge.full{stroke:#ff5d5d;stroke-opacity:.55}
.dp-map .bin rect{fill:#0d121e;stroke:rgba(255,255,255,.10);stroke-dasharray:4 4}
.dp-map .bin.full rect{stroke:#ff5d5d;stroke-opacity:.6}
.dp-map .bin-t{fill:#9aa4b8;font-size:11px;text-anchor:middle;letter-spacing:.04em;
  text-transform:uppercase}
.dp-map .bin-n{fill:#e6eaf2;font-size:12px;font-weight:600;text-anchor:middle}
.dp-map .bin.full .bin-n{fill:#ff8f8f}
.dp-flow-on .dp-map .edge.flowing{animation:none;stroke-dasharray:none;stroke-opacity:.35}
@keyframes dp-flow{to{stroke-dashoffset:-32}}
@keyframes dp-pulse{50%{stroke-width:3;stroke-opacity:.35}}
@media (prefers-reduced-motion:reduce){
  .dp-map .edge.flowing,.dp-map .is-running .box{animation:none}}
"""


def _highlight_css(keys: list[str]) -> str:
    # Al señalar un nodo se encienden las aristas que entran y salen de él.
    return "".join(
        f".dp-map:has(.n-{k}:hover) .e-{k},.dp-map:has(.n-{k}:focus) .e-{k}"
        "{stroke:#9cc4ff;stroke-opacity:1;stroke-width:3}"
        for k in keys
    )


def _curve(a: Node, b: Node) -> tuple[int, ...]:
    """Curva cúbica de la arista: inicio, dos puntos de control y fin."""
    x1, y1, x2, y2 = a.x + W, a.y + H // 2, b.x, b.y + H // 2
    bend = (x1 + x2) // 2
    return (x1, y1, bend, y1, bend, y2, x2, y2)


def _quarantine_curve(validation: Node) -> tuple[int, ...]:
    x1, y1 = validation.x + W // 2, validation.y + H
    bx, by, bw, _ = BIN
    x2, y2 = bx + bw // 2, by
    return (x1, y1, x1, y1 + 20, x2, y2 - 20, x2, y2)


def _path(c: tuple[int, ...]) -> str:
    return f"M{c[0]},{c[1]} C{c[2]},{c[3]} {c[4]},{c[5]} {c[6]},{c[7]}"


def _edge(a: Node, b: Node) -> str:
    flowing = " flowing" if a.state != "idle" else ""
    return f'<path class="edge e-{a.key} e-{b.key}{flowing}" d="{_path(_curve(a, b))}"/>'


def _rejected(batches: list[dict[str, Any]]) -> int:
    return sum(b["rejected_count"] for b in batches)


def _quarantine(validation: Node, batches: list[dict[str, Any]]) -> str:
    rejected = _rejected(batches)
    x, y, w, h = BIN
    full = " full" if rejected else ""
    return (
        f'<path class="qedge e-validation{full}" d="{_path(_quarantine_curve(validation))}"/>'
        f'<g class="bin{full}" role="img" aria-label="Cuarentena: {rejected:,} filas">'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>'
        f'<text class="bin-t" x="{x + w // 2}" y="{y + 17}">Cuarentena</text>'
        f'<text class="bin-n" x="{x + w // 2}" y="{y + 32}">{rejected:,} filas</text>'
        "</g>"
    )


def _node(n: Node) -> str:
    state = n.state if n.state in STATUS_LABEL else "idle"
    label = STATUS_LABEL.get(n.state, "sin datos")
    return (
        f'<g class="node n-{n.key} is-{state}" tabindex="0" role="img" '
        f'aria-label="{escape(n.detail)}">'
        f"<title>{escape(n.detail)}</title>"
        f'<rect class="box" x="{n.x}" y="{n.y}" width="{W}" height="{H}" rx="12"/>'
        f'<circle class="dot" cx="{n.x + 16}" cy="{n.y + 20}" r="4"/>'
        f'<text class="title" x="{n.x + 28}" y="{n.y + 25}">{escape(n.title)}</text>'
        f'<text class="line" x="{n.x + 16}" y="{n.y + 45}">{escape(n.line)}</text>'
        f'<text class="state" x="{n.x + 16}" y="{n.y + 62}">{escape(label)}</text>'
        f'<text class="detail" x="{VIEW[0] // 2}" y="{VIEW[1] - 16}">{escape(n.detail)}</text>'
        "</g>"
    )


def render(
    runs: list[dict[str, Any]], quality: list[dict[str, Any]], batches: list[dict[str, Any]]
) -> str:
    # Sin saltos de línea: st.markdown cierra el bloque HTML en una línea en blanco, y los
    # mensajes de error de la base de datos suelen traerlos.
    items = [
        replace(n, line=" ".join(n.line.split()), detail=" ".join(n.detail.split()))
        for n in nodes(runs, quality, batches)
    ]
    by_key = {n.key: n for n in items}
    summary = "; ".join(f"{n.title}: {STATUS_LABEL.get(n.state, 'sin datos')}" for n in items)
    return (
        f"<style>{_CSS}{_highlight_css(list(by_key))}</style>"
        f'<svg class="dp-map" viewBox="0 0 {VIEW[0]} {VIEW[1]}" role="group" '
        f'aria-label="Mapa del pipeline. {escape(summary)}">'
        + "".join(_edge(by_key[a], by_key[b]) for a, b in EDGES)
        + _quarantine(by_key["validation"], batches)
        + f'<text class="hint" x="{VIEW[0] // 2}" y="{VIEW[1] - 16}">'
        "Pasa el puntero por un nodo para ver su detalle</text>"
        + "".join(_node(n) for n in items)
        + "</svg>"
    )


# Flujo de cada fuente de ingesta → su nodo en el mapa.
_SOURCES: Final = {
    "customers": "ingest_customers",
    "orders": "ingest_orders",
    "products": "ingest_products",
}


def _share(part: float, whole: float) -> float:
    return round(min(1.0, part / whole), 4) if whole > 0 else 0.0


def flow_model(
    runs: list[dict[str, Any]], quality: list[dict[str, Any]], batches: list[dict[str, Any]]
) -> dict[str, Any]:
    """Lo que necesita la capa de partículas: geometría, ritmos y proporciones.

    Solo números y claves fijas, nunca texto de la API. Cada fuente emite partículas en
    proporción a las filas que leyó en su última ejecución; una parte igual a su tasa de
    rechazo se desvía a la cuarentena. En dbt → marts, la parte con aviso sale en ámbar.
    """
    by_key = {n.key: n for n in nodes(runs, quality, batches)}
    read: dict[str, int] = {}
    rejected: dict[str, float] = {}
    for key, flow in _SOURCES.items():
        run = _latest(runs, flow)
        read[key] = (run["rows_read"] or 0) if run else 0
        rejected[key] = _share(run["rows_rejected"] or 0, read[key]) if run else 0.0
    most = max(read.values(), default=0)
    post = [q for q in quality if q["layer"] == "post_transform"]
    warned = sum(q["rows_failed"] for q in post if q["severity"] == "warn")
    evaluated = sum(q.get("rows_evaluated") or 0 for q in post)
    edges = []
    for a, b in EDGES:
        src = by_key[a]
        # Ritmo relativo (de 0 a 1): las fuentes, por filas leídas; el resto hereda lo que llega.
        rate = round((read[a] / most) ** 0.5, 4) if a in read and most else 1.0
        edges.append(
            {
                "from": a,
                "to": b,
                "curve": list(_curve(src, by_key[b])),
                "flowing": src.state != "idle",
                "rate": rate if src.state != "idle" else 0.0,
                "running": src.state == "running" or by_key[b].state == "running",
            }
        )
    return {
        "view": list(VIEW),
        "edges": edges,
        "quarantine": {
            "curve": list(_quarantine_curve(by_key["validation"])),
            "share": rejected,
            "rows": _rejected(batches),
        },
        "warn_share": _share(warned, evaluated) if warned else 0.0,
    }
