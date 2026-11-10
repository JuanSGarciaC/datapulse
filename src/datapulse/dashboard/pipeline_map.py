"""Mapa del pipeline: datos de la API → SVG (función pura, sin Streamlit).

Fuentes → validación → raw → dbt → marts. Cada nodo lleva el estado de su última
ejecución. Las aristas solo fluyen si por ellas ya pasaron datos, y un nodo late mientras
su flujo está en curso: el movimiento informa, no decora.

Todo texto que viene de la API pasa por ``html.escape``: el resultado se inserta como HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Final

from datapulse.dashboard.formatting import STATUS_LABEL

W: Final = 190
H: Final = 72
VIEW: Final = (1200, 340)


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
        detail += f" · {run['error_type']}: {run['error_message']}"[:120]
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
.dp-map .is-running{--c:#5b9dff}.dp-map .is-idle{--c:#6b7488}
.dp-map .is-running .box{stroke:var(--c);animation:dp-pulse 1.4s ease-in-out infinite}
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


def _edge(a: Node, b: Node) -> str:
    x1, y1, x2, y2 = a.x + W, a.y + H // 2, b.x, b.y + H // 2
    bend = (x1 + x2) // 2
    flowing = " flowing" if a.state != "idle" else ""
    return (
        f'<path class="edge e-{a.key} e-{b.key}{flowing}" '
        f'd="M{x1},{y1} C{bend},{y1} {bend},{y2} {x2},{y2}"/>'
    )


def _node(n: Node) -> str:
    state = n.state if n.state in STATUS_LABEL else "idle"
    label = STATUS_LABEL.get(n.state, "sin datos")
    return (
        f'<g class="node n-{n.key} is-{state}" tabindex="0">'
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
    items = nodes(runs, quality, batches)
    by_key = {n.key: n for n in items}
    summary = "; ".join(f"{n.title}: {STATUS_LABEL.get(n.state, 'sin datos')}" for n in items)
    return (
        f"<style>{_CSS}{_highlight_css(list(by_key))}</style>"
        f'<svg class="dp-map" viewBox="0 0 {VIEW[0]} {VIEW[1]}" role="img" '
        f'aria-label="Mapa del pipeline. {escape(summary)}">'
        + "".join(_edge(by_key[a], by_key[b]) for a, b in EDGES)
        + f'<text class="hint" x="{VIEW[0] // 2}" y="{VIEW[1] - 16}">'
        "Pasa el puntero por un nodo para ver su detalle</text>"
        + "".join(_node(n) for n in items)
        + "</svg>"
    )
