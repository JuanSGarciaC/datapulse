"""Dashboard: cliente HTTP, gráficos y mapa (funciones puras) y página con AppTest."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2 as httpx
import pytest
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from datapulse.dashboard import charts, pipeline_map, theme
from datapulse.dashboard.client import ApiClient, ApiError, DashboardSettings
from datapulse.dashboard.formatting import money_compact as _money_compact
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


def _page(fake: FakeClient) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_client"] = fake
    return at.run()


# ------------------------------------------------------------------ página
def test_page_renders_three_tabs_with_metrics() -> None:
    at = _page(FakeClient())
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Negocio", "Calidad de datos", "Pipelines"]
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
    assert not at.button  # sin clave de administración no hay disparo


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
    assert [b.label for b in at.button] == [
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
