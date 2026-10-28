"""Dashboard: cliente HTTP, gráficos (funciones puras) y página completa con AppTest."""

from __future__ import annotations

import json
import secrets
from datetime import date
from typing import Any

import httpx2 as httpx
import pytest
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from datapulse.dashboard import charts
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


class FakeClient:
    """Implementa la interfaz que usa la página, con datos fijos."""

    def __init__(self, *, fail_quality: bool = False, empty: bool = False) -> None:
        self.fail_quality = fail_quality
        self.empty = empty

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

    def top_products(self, *_: Any) -> list[dict[str, Any]]:
        return [{"product_id": 7, "title": "Pro Laptops 007", "category": "laptops",
                 "units": 1, "revenue": "899.50"}]  # fmt: skip

    def quality_summary(self, *_: Any) -> list[dict[str, Any]]:
        if self.fail_quality:
            raise ApiError("La API respondió 503.")
        return QUALITY

    def failed_quality_results(self, *_: Any) -> list[dict[str, Any]]:
        return []

    def volumes(self, *_: Any) -> list[dict[str, Any]]:
        return VOLUMES

    def pipeline_runs(self, *_: Any) -> list[dict[str, Any]]:
        return RUNS

    def run_detail(self, run_id: str) -> dict[str, Any]:
        return RUNS[0] | {"tasks": [], "quality_failures": 2, "params": {}}


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
    assert metrics["❌ Filas con error (cuarentena)"] == "3"
    assert metrics["⚠️ Filas con aviso"] == "2"
    assert metrics["❌ Con fallo"] == "1"
    assert metrics["Filas rechazadas"] == "10"
    assert any("DbtBuildError" in e.value for e in at.error)  # detalle de la ejecución fallida


def test_api_failure_in_one_tab_does_not_break_the_others() -> None:
    at = _page(FakeClient(fail_quality=True))
    assert not at.exception
    assert [e.value for e in at.error] == [
        "La API respondió 503.",
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
