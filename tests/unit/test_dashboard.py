"""Dashboard: cliente HTTP, gráficos y mapa (funciones puras) y página con AppTest."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from datapulse.api.app import VALIDATORS, _preview
from datapulse.dashboard import charts, pipeline_map, theme
from datapulse.dashboard.client import ApiClient, ApiError, DashboardSettings, SourceChaos
from datapulse.dashboard.formatting import money_compact as _money_compact
from datapulse.mock_source.app import MockSettings
from datapulse.mock_source.app import create_app as create_mock
from tests.conftest import ROOT

APP = str(ROOT / "src/datapulse/dashboard/app.py")
KEY = secrets.token_urlsafe(24)
RUN_ID = "7d4c2f9e-1b2a-4c3d-9e8f-0a1b2c3d4e5f"

# Respuestas con la forma JSON real de la API (Decimal llega como texto).
DAILY = [
    {"order_date": "2026-01-01", "category": "beauty", "orders": 3, "units": 5,
     "revenue": "100.50", "cancelled_or_returned_orders": 0},
    {"order_date": "2026-01-01", "category": "laptops", "orders": 1, "units": 1,
     "revenue": "899.50", "cancelled_or_returned_orders": 1},
    {"order_date": "2026-01-02", "category": "beauty", "orders": 2, "units": 2,
     "revenue": "40.00", "cancelled_or_returned_orders": 0},
]  # fmt: skip
VOLUMES = [
    {"day": "2026-01-01", "flow_name": "ingest", "runs": 2, "failed_runs": 1, "rows_read": 300,
     "rows_loaded": 290, "rows_rejected": 10, "avg_duration_s": 2.0, "max_duration_s": 3.0},
    {"day": "2026-01-02", "flow_name": "ingest", "runs": 1, "failed_runs": 0, "rows_read": 100,
     "rows_loaded": 100, "rows_rejected": 0, "avg_duration_s": 4.0, "max_duration_s": 4.0},
    {"day": "2026-01-02", "flow_name": "transform", "runs": 1, "failed_runs": 0, "rows_read": 0,
     "rows_loaded": 0, "rows_rejected": 0, "avg_duration_s": None, "max_duration_s": None},
]  # fmt: skip
QUALITY = [
    {"layer": "pre_load", "table_name": "raw.customers", "rule_name": "not_null:email",
     "severity": "error", "checks": 4, "rows_evaluated": 230, "rows_failed": 3,
     "last_checked_at": "2026-01-03T10:00:00Z", "last_passed": True},
    {"layer": "post_transform", "table_name": "marts.fct_order_items",
     "rule_name": "relationships:product", "severity": "warn", "checks": 1,
     "rows_evaluated": 1, "rows_failed": 2, "last_checked_at": "2026-01-03T10:00:00Z",
     "last_passed": False},
    {"layer": "pre_load", "table_name": "raw.orders", "rule_name": "not_null:order_id",
     "severity": "error", "checks": 3, "rows_evaluated": 460, "rows_failed": 0,
     "last_checked_at": "2026-01-03T10:00:00Z", "last_passed": True},
]  # fmt: skip
RUNS = [
    {"run_id": RUN_ID, "flow_name": "daily", "source_name": None, "status": "failed",
     "trigger": "schedule", "started_at": "2026-01-03T06:00:00Z",
     "finished_at": "2026-01-03T06:00:05Z", "duration_s": 5.25, "rows_read": 10,
     "rows_loaded": 0, "rows_rejected": 0, "error_type": "DbtBuildError",
     "error_message": "2 pruebas fallaron"},
]  # fmt: skip


BATCHES = [
    {"batch_id": "0b0d3c1e-0000-4000-8000-000000000001", "source_name": "customers_csv",
     "source_ref": "customers_2026-01-01.csv", "content_sha256": "ab" * 32, "status": "success",
     "row_count": 80, "rejected_count": 3, "run_id": RUN_ID,
     "created_at": "2026-01-03T06:00:01Z", "updated_at": "2026-01-03T06:00:02Z"},
]  # fmt: skip

QUARANTINE = [
    {"quarantine_id": 2, "source_name": "customers_csv", "source_ref": "customers_2026-01-01.csv",
     "batch_id": "0b0d3c1e-0000-4000-8000-000000000001", "content_sha256": "2ca922fd" + "0" * 56,
     "run_id": RUN_ID, "row_number": 9, "record_key": "C00009",
     "errors": [{"rule": "format:email", "field": "email", "message": "no cumple el patrón"}],
     "payload_masked": {"customer_id": "C00009", "email": "na***"},
     "quarantined_at": "2026-01-03T06:00:01Z"},
    {"quarantine_id": 1, "source_name": "orders_json", "source_ref": "orders_batch_002.json",
     "batch_id": "0b0d3c1e-0000-4000-8000-000000000002", "content_sha256": "20bddfbb" + "0" * 56,
     "run_id": RUN_ID, "row_number": 81, "record_key": "O000231",
     "errors": [{"rule": "range:items.quantity", "field": "items.quantity",
                 "message": "debe ser > 0, llegó 0"}],
     "payload_masked": {"order_id": "O000231"}, "quarantined_at": "2026-01-03T06:00:02Z"},
]  # fmt: skip


def _version(rank: int, status: str, updated: str, ref: str, sha: str) -> dict[str, Any]:
    return {"version_rank": rank, "is_current": rank == 1, "status": status,
            "customer_id": "C00102", "order_ts": "2026-01-01T10:00:00Z", "updated_at": updated,
            "ingested_at": "2026-01-03T06:00:01Z", "item_count": 2, "batch_id": RUN_ID,
            "source_ref": ref, "row_number": 65, "content_sha256": sha, "run_id": RUN_ID,
            "flow_name": "ingest_orders", "trigger": "schedule",
            "run_started_at": "2026-01-03T06:00:00Z"}  # fmt: skip


LINEAGE = {
    "order_id": "O000065",
    "versions": [
        _version(2, "paid", "2026-01-01T15:56:00Z", "orders_batch_001.json", "eeca346e" * 8),
        _version(1, "cancelled", "2026-01-03T19:53:00Z", "orders_batch_003.json", "97dbbfca" * 8),
    ],
    "fact_lines": [
        {"line_no": 1, "product_id": 8, "quantity": 4, "unit_price": "1300.10",
         "line_amount": "5200.40", "status": "cancelled", "is_revenue": False,
         "order_date": "2026-01-01", "batch_id": RUN_ID},
    ],
    "quarantined": [],
}  # fmt: skip

FRESHNESS: list[dict[str, Any]] = [
    {"source_name": "customers_csv", "last_success_at": "2026-01-03T06:00:05Z",
     "last_attempt_at": "2026-01-03T06:00:00Z", "last_status": "partial", "age_hours": 3.2,
     "sla_hours": 26.0, "status": "fresh"},
    {"source_name": "products_api", "last_success_at": "2026-01-01T06:00:05Z",
     "last_attempt_at": "2026-01-03T06:00:00Z", "last_status": "failed", "age_hours": 51.4,
     "sla_hours": 26.0, "status": "stale"},
    {"source_name": "orders_json", "last_success_at": None,
     "last_attempt_at": "2026-01-03T06:00:00Z", "last_status": "failed", "age_hours": None,
     "sla_hours": 26.0, "status": "never"},
]  # fmt: skip


class FakeClient:
    """Implementa la interfaz que usa la página, con datos fijos."""

    def __init__(
        self, *, fail_quality: bool = False, empty: bool = False, admin: bool = False
    ) -> None:
        self.fail_quality = fail_quality
        self.empty = empty
        self.can_trigger = admin
        self.triggered: list[str] = []
        self.calls: list[tuple[Any, ...]] = []

    def batches(self, *_: Any) -> list[dict[str, Any]]:
        return BATCHES

    def trigger(self, name: str) -> dict[str, Any]:
        if name == "products":
            raise ApiError("La API respondió 503. el orquestador no está disponible")
        self.triggered.append(name)
        return {"deployment": name, "flow_run_id": RUN_ID, "state": "SCHEDULED"}

    def ready(self) -> dict[str, Any]:
        return {"status": "ok", "database": "ok"}

    def kpis(self, *_: Any) -> dict[str, Any]:
        return {"date_from": "2026-01-01", "date_to": "2026-01-03", "revenue": "1040.00",
                "orders": 6, "units": 8, "average_order_value": "173.33",
                "active_customers": 5, "cancelled_or_returned_orders": 1}  # fmt: skip

    def daily_sales(self, *_: Any) -> list[dict[str, Any]]:
        return [] if self.empty else DAILY

    def sales_by_category(self, *_: Any) -> list[dict[str, Any]]:
        return [] if self.empty else [
            {"category": "laptops", "orders": 1, "units": 1, "revenue": "899.50"},
            {"category": "beauty", "orders": 5, "units": 7, "revenue": "140.50"},
        ]  # fmt: skip

    def top_products(self, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(("top_products", *args))
        return [{"product_id": 7, "title": "Pro Laptops 007", "category": "laptops",
                 "units": 1, "revenue": "899.50"}]  # fmt: skip

    def quality_summary(self, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(("quality_summary", *args))
        if self.fail_quality:
            raise ApiError("La API respondió 503.")
        return QUALITY

    def failed_quality_results(self, *_: Any) -> list[dict[str, Any]]:
        return []

    def volumes(self, *_: Any) -> list[dict[str, Any]]:
        return VOLUMES

    def pipeline_runs(self, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(("pipeline_runs", *args))
        return RUNS

    def run_detail(self, run_id: str) -> dict[str, Any]:
        return RUNS[0] | {"tasks": [], "quality_failures": 2, "params": {"trigger": "api"}}

    def quarantine(self) -> list[dict[str, Any]]:
        return [] if self.empty else QUARANTINE

    def freshness(self) -> list[dict[str, Any]]:
        return FRESHNESS

    def order_lineage(self, order_id: str) -> dict[str, Any]:
        if order_id != "O000065":
            raise ApiError("La API respondió 404. pedido no encontrado")
        return LINEAGE

    def validate_file(self, source: str, data: bytes) -> dict[str, Any]:
        # Las mismas funciones que usa la API, sin HTTP: el veredicto es el real.
        return _preview(VALIDATORS[source](data)).model_dump()


class FakeChaos:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[str] = []

    def break_source(self) -> None:
        self.calls.append("break")

    def restore(self) -> None:
        self.calls.append("restore")


def _page(fake: FakeClient, chaos: FakeChaos | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_client"] = fake
    at.session_state["_chaos"] = chaos or FakeChaos(available=False)
    return at.run()


# ------------------------------------------------------------------ página
def test_page_renders_its_tabs_with_metrics() -> None:
    at = _page(FakeClient())
    assert not at.exception
    assert [t.label for t in at.tabs] == [
        "Negocio",
        "Calidad de datos",
        "Pipelines",
        "Probar contratos",
        "Linaje",
    ]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Ingresos"] == "$ 1.04 k"
    assert metrics["Pedidos"] == "6"
    assert metrics["Unidades"] == "8"
    assert metrics["Filas con error (cuarentena)"] == "3"
    assert metrics["Filas con aviso"] == "2"
    assert metrics["Con fallo"] == "1"
    assert metrics["Filas rechazadas"] == "10"
    assert any("DbtBuildError" in e.value for e in at.error)  # detalle de la ejecución fallida
    assert any("Fallos de calidad: 2" in c.value for c in at.caption)
    # Los lotes de ingesta tienen pantalla, con el estado en texto.
    lotes = [d.value for d in at.dataframe if "huella" in d.value.columns]
    assert lotes[0].iloc[0]["referencia"] == "customers_2026-01-01.csv"
    assert lotes[0].iloc[0]["estado"] == "éxito"
    assert any("dp-map" in m.value for m in at.markdown)  # el mapa se dibuja
    # Sin clave de administración no hay disparo.
    assert not [b for b in at.button if str(b.key).startswith("trigger_")]


def test_no_emojis_in_labels() -> None:
    at = _page(FakeClient())
    texts = [m.label for m in at.metric] + [t.label for t in at.tabs]
    assert all(ch.isascii() or ch.isalpha() for text in texts for ch in text), texts


def test_filters_reach_the_api() -> None:
    fake = FakeClient()
    at = _page(fake)
    at.radio(key="top_metric").set_value("units")
    at.radio(key="quality_layer").set_value("pre_load")
    at.selectbox(key="runs_status").set_value("failed").run()
    assert not at.exception
    assert ("top_products", 10, date(2026, 1, 1), date(2026, 1, 3), "units") in fake.calls
    assert ("quality_summary", "pre_load") in fake.calls
    assert ("pipeline_runs", 100, "failed") in fake.calls


def test_trigger_needs_admin_key_and_starts_the_live_refresh() -> None:
    fake = FakeClient(admin=True)
    at = _page(fake)
    assert [b.label for b in at.button if str(b.key).startswith("trigger_")] == [
        "Ejecutar flujo diario",
        "Ejecutar ingesta de productos",
    ]
    at.button(key="trigger_daily").click().run()
    assert not at.exception
    assert fake.triggered == ["daily"]
    assert at.session_state["_live"] is True  # el mapa pasa a refrescarse solo


def test_a_running_flow_turns_the_refresh_on_but_an_orphan_row_does_not() -> None:
    class Running(FakeClient):
        started = datetime.now(UTC)

        def pipeline_runs(self, *_: Any) -> list[dict[str, Any]]:
            return [RUNS[0] | {"status": "running", "started_at": self.started.isoformat()}]

    assert _page(Running()).session_state["_live"] is True
    Running.started = datetime.now(UTC) - timedelta(hours=2)  # el worker murió hace horas
    assert "_live" not in _page(Running()).session_state


def test_layer_filter_also_filters_the_failed_results_table() -> None:
    class WithResults(FakeClient):
        def failed_quality_results(self, *_: Any) -> list[dict[str, Any]]:
            return [
                {"layer": layer, "table_name": "t", "rule_name": layer, "severity": "error",
                 "rows_failed": 1, "details": {}, "checked_at": "2026-01-03T10:00:00Z"}
                for layer in ("pre_load", "post_transform")
            ]  # fmt: skip

    at = _page(WithResults())
    at.radio(key="quality_layer").set_value("pre_load").run()
    tabla = next(d.value for d in at.dataframe if "rule_name" in d.value.columns)
    assert list(tabla["rule_name"]) == ["pre_load"]


def test_trigger_failure_is_shown_and_does_not_start_the_refresh() -> None:
    at = _page(FakeClient(admin=True))
    at.button(key="trigger_products").click().run()
    assert not at.exception
    assert any("orquestador" in e.value for e in at.error)
    assert "_watch_until" not in at.session_state


def test_api_failure_in_one_tab_does_not_break_the_others() -> None:
    at = _page(FakeClient(fail_quality=True))
    assert not at.exception
    assert [e.value for e in at.error] == [
        "La API respondió 503.",  # el mapa usa el resumen de calidad
        "La API respondió 503.",  # la pestaña de calidad
        "DbtBuildError: 2 pruebas fallaron",
    ]
    assert {m.label for m in at.metric} >= {"Ingresos", "Ejecuciones"}


def test_empty_range_shows_a_message_instead_of_a_chart() -> None:
    at = _page(FakeClient(empty=True))
    assert not at.exception
    assert any("Sin ventas" in i.value for i in at.info)


def test_inverted_date_range_is_rejected() -> None:
    at = _page(FakeClient())
    at.date_input[0].set_value(date(2026, 2, 1)).run()
    assert any("no puede ser posterior" in e.value for e in at.error)
    assert len(at.tabs) == 0


def test_unreachable_api_shows_a_warning() -> None:
    class Down(FakeClient):
        def ready(self) -> dict[str, Any]:
            return {"status": "unreachable", "database": None}

    at = _page(Down())
    assert any("no están disponibles" in w.value for w in at.warning)


# ------------------------------------------------------------------ mapa del pipeline
def _run(flow: str, status: str, **extra: Any) -> dict[str, Any]:
    return RUNS[0] | {"flow_name": flow, "status": status, "error_message": None} | extra


def test_map_shows_the_latest_run_of_each_stage() -> None:
    runs = [
        _run("ingest_customers", "failed", started_at="2026-01-01T06:00:00Z"),
        _run("ingest_customers", "success", started_at="2026-01-02T06:00:00Z", rows_loaded=263),
        _run("transform", "running", started_at="2026-01-02T06:01:00Z", duration_s=None),
    ]
    by_key = {n.key: n for n in pipeline_map.nodes(runs, QUALITY, BATCHES)}
    assert by_key["customers"].state == "success"  # manda la ejecución más reciente
    assert by_key["customers"].line == "263 filas · 5.2 s"
    assert by_key["orders"].state == "idle"
    assert by_key["dbt"].state == "running"
    assert by_key["dbt"].line == "0 filas"  # en curso: aún sin duración
    idle_rows = pipeline_map.nodes(
        [_run("transform", "success", rows_read=0, rows_loaded=0)], [], []
    )
    assert idle_rows[5].line == "5.2 s"  # dbt no cuenta filas: solo la duración
    assert by_key["validation"].state == "partial"  # 3 filas con error en pre_load
    assert "3 filas con error" in by_key["validation"].detail
    assert by_key["raw"].line == "1 lotes · 80 filas"

    html = pipeline_map.render(runs, QUALITY, BATCHES)
    assert 'class="node n-dbt is-running"' in html
    # Solo fluyen las aristas por las que ya pasaron datos.
    assert 'class="edge e-customers e-validation flowing"' in html
    assert 'class="edge e-orders e-validation"' in html


def test_map_without_data_is_all_idle() -> None:
    html = pipeline_map.render([], [], [])
    assert html.count("is-idle") == 7 + 1  # siete nodos y la regla CSS
    assert 'flowing"' not in html


def test_map_escapes_text_from_the_api() -> None:
    evil = "<script>alert(1)</script>"
    html = pipeline_map.render(
        [_run("transform", "failed", error_type="X", error_message=evil, trigger=evil)], [], []
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_map_has_no_line_breaks_that_would_end_the_html_block() -> None:
    # Un error de PostgreSQL trae «\nDETAIL: ...»; una línea en blanco rompería el SVG.
    run = _run("transform", "failed", error_message="Boom\n\nDETAIL: fila 3")
    svg = pipeline_map.render([run], [], []).split("</style>")[1]
    assert "\n" not in svg
    assert "Boom DETAIL: fila 3" in svg


# ------------------------------------------------------------------ gráficos
def _spec(figure: charts.Figure) -> dict[str, Any]:
    spec: dict[str, Any] = figure.to_dict()
    return spec


def _values(spec: dict[str, Any]) -> list[dict[str, Any]]:
    data = spec.get("data") or spec["layer"][0]["data"]
    values: list[dict[str, Any]] = data["values"]
    return values


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "—"),
        ("12.5", "$ 12.50"),
        ("1040", "$ 1.04 k"),
        (2613860.45, "$ 2.61 M"),
        (-3e9, "$ -3.00 B"),
    ],
)
def test_money_compact_never_needs_truncation(value: Any, expected: str) -> None:
    assert _money_compact(value) == expected


def test_revenue_by_day_sums_categories_per_day() -> None:
    values = _values(_spec(charts.revenue_by_day(DAILY)))
    assert values == [
        {"day": "2026-01-01", "revenue": 1000.0},
        {"day": "2026-01-02", "revenue": 40.0},
    ]


def test_revenue_by_day_does_not_shift_days_with_the_browser_timezone() -> None:
    spec = _spec(charts.revenue_by_day(DAILY))
    assert spec["layer"][0]["encoding"]["x"]["scale"]["type"] == "utc"
    tooltip = spec["layer"][-1]["encoding"]["tooltip"][0]
    assert tooltip["timeUnit"] == "utcyearmonthdate"


def test_theme_css_survives_the_html_sanitizer() -> None:
    # El saneador de st.html descarta una hoja de estilo que contenga algo con forma de etiqueta.
    assert "<" not in theme.CSS
    assert "<" not in theme.JS


def test_single_series_charts_have_no_legend_and_use_primary_color() -> None:
    spec = _spec(charts.revenue_by_category([{"category": "a", "orders": 1, "revenue": "1"}]))
    assert spec["mark"]["color"] == charts.SERIES[0]
    assert "color" not in spec["encoding"]


def test_quality_chart_uses_status_colors_and_skips_passing_rules() -> None:
    spec = _spec(charts.quality_failures(QUALITY))
    assert len(_values(spec)) == 2  # la regla sin fallos no aparece
    scale = spec["encoding"]["color"]["scale"]
    assert scale["range"] == [charts.STATUS["error"], charts.STATUS["warn"]]
    assert scale["domain"] == ["Error (cuarentena)", "Aviso"]


def test_rows_per_day_has_two_labelled_series() -> None:
    spec = _spec(charts.rows_per_day(VOLUMES))
    day2 = {v["kind"]: v["rows"] for v in _values(spec) if v["day"] == "2026-01-02"}
    assert day2 == {"Cargadas": 100, "Rechazadas": 0}
    assert spec["encoding"]["color"]["scale"]["domain"] == ["Cargadas", "Rechazadas"]


def test_duration_by_flow_ignores_flows_without_duration() -> None:
    values = _values(_spec(charts.duration_by_flow(VOLUMES)))
    assert values == [{"flow": "ingest", "seconds": 3.0, "max": 4.0}]


# ------------------------------------------------------------------ cliente HTTP
def _client(handler: Any) -> tuple[ApiClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        response: httpx.Response = handler(request)
        return response

    http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(record))
    return ApiClient("http://api", SecretStr(KEY), http=http), seen


def test_client_sends_key_in_header_and_serializes_dates() -> None:
    client, seen = _client(lambda r: httpx.Response(200, json={"revenue": "1"}))
    client.kpis(date(2026, 1, 1), None)
    assert seen[0].headers["X-API-Key"] == KEY
    assert dict(seen[0].url.params) == {"date_from": "2026-01-01"}  # None se omite
    assert KEY not in str(seen[0].url)  # la clave nunca viaja en la URL


def test_client_sends_the_new_filters() -> None:
    page = {"items": [], "limit": 100, "offset": 0, "total": 0}
    client, seen = _client(lambda r: httpx.Response(200, json=page))
    client.daily_sales(category="beauty")
    client.pipeline_runs(50, "failed")
    client.batches()
    assert seen[0].url.params["category"] == "beauty"
    assert dict(seen[1].url.params) == {"limit": "50", "status": "failed"}
    assert seen[2].url.path == "/v1/ingestion/batches"
    client, seen = _client(lambda r: httpx.Response(200, json=[]))
    client.top_products(metric="units")
    assert seen[0].url.params["metric"] == "units"


def test_trigger_uses_the_admin_key_and_is_off_without_it() -> None:
    admin = secrets.token_urlsafe(24)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"deployment": "d", "flow_run_id": RUN_ID, "state": "S"})

    http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    client = ApiClient("http://api", SecretStr(KEY), http=http, admin_key=SecretStr(admin))
    assert client.can_trigger
    assert client.trigger("daily")["state"] == "S"
    assert (seen[0].method, seen[0].url.path) == ("POST", "/v1/admin/pipelines/daily/runs")
    assert seen[0].headers["X-API-Key"] == admin  # nunca la clave de lectura

    # Sin clave, o con la variable vacía, el panel es de solo lectura y no llama a la API.
    for missing in (None, SecretStr("")):
        readonly = ApiClient("http://api", SecretStr(KEY), http=http, admin_key=missing)
        assert not readonly.can_trigger
        with pytest.raises(ApiError, match="solo lectura"):
            readonly.trigger("daily")
    assert len(seen) == 1


def test_client_follows_pagination_until_total() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        items = [{"i": i} for i in range(offset, min(offset + 500, 1200))]
        return httpx.Response(
            200, json={"items": items, "limit": 500, "offset": offset, "total": 1200}
        )

    client, seen = _client(handler)
    assert len(client.daily_sales()) == 1200
    assert [r.url.params["offset"] for r in seen] == ["0", "500", "1000"]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, json={"detail": "x"}), "rechazó la clave"),
        (httpx.Response(422, json={"detail": "fecha inválida"}), "422. fecha inválida"),
        (httpx.Response(500, text="<html>traza interna</html>"), "La API respondió 500."),
    ],
)
def test_client_turns_http_errors_into_user_messages(
    response: httpx.Response, message: str
) -> None:
    client, _ = _client(lambda _: response)
    with pytest.raises(ApiError, match=message):
        client.quality_summary()


def test_client_reports_connection_errors_and_unreachable_health() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, _ = _client(boom)
    with pytest.raises(ApiError, match="No se puede conectar"):
        client.volumes()
    assert client.ready() == {"status": "unreachable", "database": None}


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAPULSE_DASHBOARD_API_URL", "http://api:8000")
    monkeypatch.setenv("DATAPULSE_DASHBOARD_API_KEY", KEY)
    settings = DashboardSettings(_env_file=None)
    assert settings.api_url == "http://api:8000"
    assert KEY not in repr(settings)
    assert settings.admin_key is None  # el disparo es opcional y viene apagado
    assert isinstance(ApiClient.from_settings(settings), ApiClient)


def test_entrypoint_builds_hardened_streamlit_command(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys  # noqa: PLC0415

    from datapulse.dashboard import __main__ as entry  # noqa: PLC0415

    captured: list[str] = []

    def fake_main() -> int:
        captured.extend(sys.argv)
        return 0

    monkeypatch.setattr("streamlit.web.cli.main", fake_main)
    monkeypatch.setenv("DATAPULSE_DASHBOARD_PORT", "9501")
    with pytest.raises(SystemExit):
        entry.main()
    args = dict(zip(captured[3::2], captured[4::2], strict=False))
    assert captured[1:3] == ["run", APP]
    assert args["--server.port"] == "9501"
    assert args["--browser.gatherUsageStats"] == "false"
    assert args["--server.enableXsrfProtection"] == "true"
    assert json.loads(args["--server.headless"]) is True
    assert args["--client.toolbarMode"] == "minimal"
    assert args["--theme.base"] == "dark"


# ------------------------------------------------------------------ validador en vivo
def test_contract_example_shows_which_rows_go_to_quarantine_and_why() -> None:
    at = _page(FakeClient())
    assert any("Sube un archivo" in i.value for i in at.info)
    at.button(key="contract_example_customers").click().run()
    assert not at.exception
    table = next(
        d.value
        for d in at.dataframe
        if "motivo" in d.value.columns and "lote" not in d.value.columns
    )
    assert set(table["clave"]) == {"C00009", "C00014", "C00062"}
    assert set(table["campo"]) == {"email", "signup_date"}
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["A cuarentena"] == "3"
    assert any("3 filas irían a cuarentena" in w.value for w in at.warning)


def test_contract_example_for_orders() -> None:
    at = _page(FakeClient())
    at.radio(key="contract_source").set_value("orders").run()
    at.button(key="contract_example_orders").click().run()
    table = next(
        d.value
        for d in at.dataframe
        if "motivo" in d.value.columns and "lote" not in d.value.columns
    )
    assert set(table["clave"]) == {"O000170", "O000231"}


def test_contract_examples_are_exact_copies_of_sample_rows() -> None:
    examples = ROOT / "src/datapulse/dashboard/examples"
    sample = (ROOT / "data/samples/customers/customers_2026-01-01.csv").read_text(encoding="utf-8")
    sample_lines = set(sample.splitlines())
    assert (
        set((examples / "clientes_con_defectos.csv").read_text(encoding="utf-8").splitlines())
        <= sample_lines
    )
    batch = json.loads(
        (ROOT / "data/samples/orders/orders_batch_002.json").read_text(encoding="utf-8")
    )
    example = json.loads((examples / "pedidos_con_defectos.json").read_text(encoding="utf-8"))
    assert all(order in batch["orders"] for order in example["orders"])


# ------------------------------------------------------------------ bandeja de cuarentena
def _tray(at: AppTest) -> Any:
    return next(d.value for d in at.dataframe if "lote" in d.value.columns)


def test_quarantine_tray_lists_reason_file_and_masked_value() -> None:
    at = _page(FakeClient())
    tray = _tray(at)
    assert list(tray["clave"]) == ["C00009", "O000231"]
    assert tray.iloc[0]["valor"] == "na***"  # el email llega enmascarado desde la API
    assert tray.iloc[1]["valor"] == "—"  # campo anidado: no se inventa un valor
    assert tray.iloc[0]["archivo"] == "customers_2026-01-01.csv"


def test_quarantine_tray_filters_by_field_and_key() -> None:
    at = _page(FakeClient())
    at.selectbox(key="q_field").set_value("email").run()
    assert list(_tray(at)["clave"]) == ["C00009"]
    at.selectbox(key="q_field").set_value(None).run()
    at.text_input(key="q_key").input("o000231").run()
    assert list(_tray(at)["clave"]) == ["O000231"]


def test_empty_quarantine_says_so() -> None:
    at = _page(FakeClient(empty=True))
    assert any("No hay filas en cuarentena" in s.value for s in at.success)


# ------------------------------------------------------------------ linaje
def _markdown(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def test_lineage_follows_an_order_from_the_fact_to_its_file_and_run() -> None:
    at = _page(FakeClient())
    assert not at.exception
    text = _markdown(at)
    assert "no cuenta como ingreso (cancelled)" in text
    assert "orders_batch_003.json" in text  # archivo de la versión vigente
    assert "97dbbfca" * 8 in text  # huella completa del lote
    assert "ingest_orders" in text
    versions = next(d.value for d in at.dataframe if "versión" in d.value.columns)
    assert list(versions["versión"]) == ["superada", "vigente"]
    assert list(versions["status"]) == ["paid", "cancelled"]


def test_lineage_validates_the_id_and_handles_unknown_orders() -> None:
    at = _page(FakeClient())
    at.text_input(key="lineage_id").input("hola").run()
    assert any("O seguida de 6 dígitos" in e.value for e in at.error)
    at.text_input(key="lineage_id").input("o999999").run()
    assert any("No hay rastro de O999999" in i.value for i in at.info)


# ------------------------------------------------------------------ caos en un clic
class _ProductsOk(FakeClient):
    def trigger(self, name: str) -> dict[str, Any]:
        self.triggered.append(name)
        return {"deployment": name, "flow_run_id": RUN_ID, "state": "SCHEDULED"}


def test_chaos_buttons_only_with_admin_key_and_mock_configured() -> None:
    keys = {str(b.key) for b in _page(FakeClient(admin=True)).button}
    assert "chaos_break" not in keys  # sin configuración de la demo
    keys = {str(b.key) for b in _page(FakeClient(), FakeChaos()).button}
    assert "chaos_break" not in keys  # sin clave de administración


def test_chaos_breaks_the_source_then_runs_products() -> None:
    fake, chaos = _ProductsOk(admin=True), FakeChaos()
    at = _page(fake, chaos)
    at.button(key="chaos_break").click().run()
    assert not at.exception
    assert chaos.calls == ["break"]
    assert fake.triggered == ["products"]
    assert at.session_state["_watch_until"] > 0  # el mapa se refresca solo
    assert any("fallará 6 veces" in i.value for i in at.info)
    at.button(key="chaos_restore").click().run()
    assert chaos.calls == ["break", "restore"]
    assert not any("fallará 6 veces" in i.value for i in at.info)


def test_source_chaos_against_the_real_mock() -> None:
    token = secrets.token_urlsafe(24)
    mock = create_mock(
        MockSettings(
            _env_file=None,
            admin_token=SecretStr(token),
            catalog_path=ROOT / "data/samples/products/products_catalog.json",
        )
    )
    with TestClient(mock) as http:
        chaos = SourceChaos("http://testserver", SecretStr(token), http=http)
        assert chaos.available
        chaos.break_source()
        codes = [http.get("/products", params={"skip": 0}).status_code for _ in range(7)]
        assert codes == [503] * SourceChaos.FAILURES + [200]
        chaos.break_source()  # se puede repetir: el contador vuelve a empezar
        assert http.get("/products", params={"skip": 0}).status_code == 503
        chaos.restore()
        assert http.get("/products", params={"skip": 0}).status_code == 200
        wrong = SourceChaos("http://testserver", SecretStr("x" * 24), http=http)
        with pytest.raises(ApiError, match="401"):
            wrong.break_source()


def test_source_chaos_is_off_without_url_or_token() -> None:
    assert not SourceChaos(None, SecretStr("x" * 24)).available
    assert not SourceChaos("http://127.0.0.1:8106", None).available
    with pytest.raises(ApiError, match="no está configurada"):
        SourceChaos(None, None).break_source()


# ------------------------------------------------------------------ frescura
def test_map_shows_freshness_badges_per_source() -> None:
    svg = pipeline_map.render(RUNS, QUALITY, BATCHES, FRESHNESS)
    assert '<text class="fresh f-fresh"' in svg
    assert ">al día<" in svg
    assert ">atrasada 51 h<" in svg
    assert ">sin cargas<" in svg
    assert "última carga buena hace 51 h (SLA 26 h)" in svg  # en el detalle accesible
    assert svg.count('class="fresh ') == 3  # solo los nodos de fuente


def test_map_without_freshness_has_no_badges() -> None:
    assert 'class="fresh ' not in pipeline_map.render(RUNS, QUALITY, BATCHES)


# ------------------------------------------------------------------ tendencia de calidad
def _ingest(source: str | None, when: str, read: int, rejected: int) -> dict[str, Any]:
    return RUNS[0] | {"source_name": source, "started_at": when, "rows_read": read,
                      "rows_rejected": rejected, "status": "partial"}  # fmt: skip


def test_quarantine_trend_is_rate_per_ingest_run_with_the_budget_line() -> None:
    runs = [
        _ingest("orders_json", "2026-01-02T06:00:00Z", 155, 4),
        _ingest("customers_csv", "2026-01-01T06:00:00Z", 200, 7),
        _ingest(None, "2026-01-02T06:05:00Z", 10, 0),  # transform: no es una ingesta
        _ingest("products_api", "2026-01-03T06:00:00Z", 0, 0),  # sin filas: sin punto
    ]
    spec = _spec(charts.quarantine_rate_by_run(runs))
    values = _values(spec)
    assert [(v["source"], round(v["rate"], 3)) for v in values] == [
        ("customers_csv", 0.035),  # ordenadas por inicio
        ("orders_json", 0.026),
    ]
    color = spec["layer"][0]["encoding"]["color"]["scale"]
    assert color["domain"] == list(charts.SOURCES)  # colores fijos por fuente
    assert spec["layer"][1]["data"]["values"] == [{"budget": charts.ERROR_BUDGET}]
    assert spec["layer"][1]["mark"]["color"] == charts.STATUS["error"]


def test_quality_tab_shows_the_trend_or_says_there_is_none() -> None:
    class WithIngests(FakeClient):
        def pipeline_runs(self, *args: Any) -> list[dict[str, Any]]:
            return [*RUNS, _ingest("orders_json", "2026-01-02T06:00:00Z", 155, 4)]

    at = _page(WithIngests())
    assert any("presupuesto" in c.value for c in at.caption)
    assert not any("Aún no hay ingestas" in i.value for i in at.info)
    assert any("Aún no hay ingestas" in i.value for i in _page(FakeClient()).info)
