"""API sin base de datos: autenticación, validación, cabeceras, errores y disparo admin."""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine

from datapulse.api.app import MAX_UPLOAD_BYTES, _freshness, create_app
from datapulse.api.security import KeyChecker
from datapulse.api.settings import ApiSettings

# Claves generadas en cada ejecución: ningún literal con forma de secreto en el repositorio.
READ_KEY = secrets.token_urlsafe(24)
ADMIN_KEY = secrets.token_urlsafe(24)
WRONG_KEY = secrets.token_urlsafe(24)
OLD_KEY = secrets.token_urlsafe(24)
NEW_KEY = secrets.token_urlsafe(24)
OTHER_KEY = secrets.token_urlsafe(24)


def settings(**overrides: Any) -> ApiSettings:
    base: dict[str, Any] = {"keys": [SecretStr(READ_KEY)], "admin_key": SecretStr(ADMIN_KEY)}
    return ApiSettings(_env_file=None, **(base | overrides))


@pytest.fixture
def down_engine() -> Any:
    # Puerto 1: la conexión se rechaza al instante (simula base de datos caída).
    engine = create_engine("postgresql+psycopg://x:y@127.0.0.1:1/x?connect_timeout=1")
    yield engine
    engine.dispose()


@pytest.fixture
def client(down_engine: Any) -> Iterator[TestClient]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_trigger(deployment: str, params: dict[str, Any]) -> tuple[uuid.UUID, str]:
        calls.append((deployment, params))
        return uuid.UUID(int=1), "SCHEDULED"

    app = create_app(settings(), engine=down_engine, trigger=fake_trigger)
    with TestClient(app) as tc:
        tc.app.state.calls = calls  # type: ignore[attr-defined]
        yield tc


# ------------------------------------------------------------------ autenticación
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": WRONG_KEY}, {"X-API-Key": ""}])
def test_business_endpoints_require_a_valid_key(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.get("/v1/kpis/summary", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "APIKey"


def test_key_in_query_string_is_not_accepted(client: TestClient) -> None:
    assert client.get(f"/v1/kpis/summary?api_key={READ_KEY}").status_code == 401


def test_admin_key_is_separate_from_read_key(client: TestClient) -> None:
    response = client.post("/v1/admin/pipelines/daily/runs", headers={"X-API-Key": READ_KEY})
    assert response.status_code == 401


def test_admin_trigger_uses_a_whitelisted_deployment(client: TestClient) -> None:
    response = client.post("/v1/admin/pipelines/daily/runs", headers={"X-API-Key": ADMIN_KEY})
    assert response.status_code == 202
    assert response.json() == {
        "deployment": "daily-pipeline/daily",
        "flow_run_id": str(uuid.UUID(int=1)),
        "state": "SCHEDULED",
    }
    assert client.app.state.calls == [("daily-pipeline/daily", {"trigger": "api"})]  # type: ignore[attr-defined]
    unknown = client.post("/v1/admin/pipelines/drop-db/runs", headers={"X-API-Key": ADMIN_KEY})
    assert unknown.status_code == 422


def test_admin_rejects_full_refresh_and_unknown_fields(client: TestClient) -> None:
    headers = {"X-API-Key": ADMIN_KEY}
    assert (
        client.post(
            "/v1/admin/pipelines/daily/runs", headers=headers, json={"full_refresh": True}
        ).status_code
        == 400
    )
    assert (
        client.post("/v1/admin/pipelines/daily/runs", headers=headers, json={"x": 1}).status_code
        == 422
    )


def test_admin_endpoint_hidden_without_admin_key(down_engine: Any) -> None:
    app = create_app(settings(admin_key=None), engine=down_engine)
    with TestClient(app) as tc:
        assert (
            tc.post("/v1/admin/pipelines/daily/runs", headers={"X-API-Key": READ_KEY}).status_code
            == 404
        )


def test_orchestrator_down_is_a_503(down_engine: Any) -> None:
    def broken(*_: Any) -> tuple[uuid.UUID, str]:
        raise ConnectionError("prefect caído")

    with TestClient(create_app(settings(), engine=down_engine, trigger=broken)) as tc:
        response = tc.post("/v1/admin/pipelines/products/runs", headers={"X-API-Key": ADMIN_KEY})
    assert response.status_code == 503
    assert "prefect" not in response.text


def test_no_keys_configured_fails_closed(down_engine: Any) -> None:
    with TestClient(create_app(ApiSettings(_env_file=None), engine=down_engine)) as tc:
        assert (
            tc.get("/v1/kpis/summary", headers={"X-API-Key": "anything-at-all-123"}).status_code
            == 401
        )


def test_key_checker_supports_rotation() -> None:
    checker = KeyChecker([SecretStr(OLD_KEY), SecretStr(NEW_KEY)])
    assert checker.valid(OLD_KEY)
    assert checker.valid(NEW_KEY)
    assert not checker.valid(OTHER_KEY)
    assert not checker.valid(None)


@pytest.mark.parametrize("bad", [{"keys": "short"}, {"admin_key": "short"}])
def test_weak_keys_are_rejected(bad: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in bad.items():
        monkeypatch.setenv(f"DATAPULSE_API_{name.upper()}", value)
    with pytest.raises(ValidationError, match="al menos 16"):
        ApiSettings(_env_file=None)


def test_keys_are_read_comma_separated_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAPULSE_API_KEYS", "aaaaaaaaaaaaaaaa1, bbbbbbbbbbbbbbbb2")
    cfg = ApiSettings(_env_file=None)
    assert [k.get_secret_value() for k in cfg.keys] == ["aaaaaaaaaaaaaaaa1", "bbbbbbbbbbbbbbbb2"]
    assert "aaaaaaaaaaaaaaaa1" not in repr(cfg)


# ------------------------------------------------------------------ validación y errores
AUTH = {"X-API-Key": READ_KEY}


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("/v1/kpis/summary?date_from=2026-02-01&date_to=2026-01-01", 400),
        ("/v1/kpis/summary?date_from=ayer", 422),
        ("/v1/sales/daily?limit=0", 422),
        ("/v1/sales/daily?limit=100000", 422),
        ("/v1/sales/daily?offset=-1", 422),
        ("/v1/products/top?metric=profit", 422),
        ("/v1/pipeline-runs?status=exploded", 422),
        ("/v1/pipeline-runs/not-a-uuid", 422),
        ("/v1/metrics/volumes?days=0", 422),
    ],
)
def test_invalid_input_is_rejected_before_touching_the_db(
    client: TestClient, url: str, status: int
) -> None:
    assert client.get(url, headers=AUTH).status_code == status


def test_database_down_is_a_clean_503(client: TestClient) -> None:
    response = client.get("/v1/kpis/summary", headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": "base de datos no disponible"}
    assert "127.0.0.1" not in response.text  # sin detalles internos


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "database": None}
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["database"] == "down"


def test_security_headers(client: TestClient) -> None:
    response = client.get("/v1/kpis/summary", headers=AUTH)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"


def test_docs_can_be_disabled(down_engine: Any) -> None:
    with TestClient(create_app(settings(docs_enabled=False), engine=down_engine)) as tc:
        assert tc.get("/docs").status_code == 404
        assert tc.get("/openapi.json").status_code == 404


def test_openapi_declares_api_key_header(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert any(
        s
        == {"type": "apiKey", "in": "header", "name": "X-API-Key", "description": "Clave de acceso"}
        for s in schemes.values()
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"keys": [SecretStr("change-me-api-key-0001")]},
        {"keys": [SecretStr(READ_KEY), SecretStr("change-me-dashboard-key-0001")]},
        {"keys": [SecretStr(READ_KEY)], "admin_key": SecretStr("change-me-admin-key-0001")},
    ],
)
def test_example_keys_are_rejected_in_prod(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    from pydantic import ValidationError  # noqa: PLC0415

    from datapulse.config import get_settings  # noqa: PLC0415

    ApiSettings(_env_file=None, **overrides)  # en dev se aceptan
    monkeypatch.setenv("DATAPULSE_ENV", "prod")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match=r"\.env\.example"):
        ApiSettings(_env_file=None, **overrides)


# ------------------------------------------------------------------ validador en vivo
SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


def _manifest_keys(file: str, kinds: set[str]) -> set[str]:
    manifest = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
    return {d["key"] for d in manifest["defects"] if d["file"] == file and d["kind"] in kinds}


def _validate(client: TestClient, source: str, body: bytes, key: str = READ_KEY) -> Any:
    return client.post(f"/v1/contracts/{source}/validate", content=body, headers={"X-API-Key": key})


ROW_DEFECTS = {"invalid_format", "invalid_type", "missing_required", "out_of_range"}


def test_preview_quarantines_exactly_the_annotated_customer_defects(client: TestClient) -> None:
    # El cliente de prueba usa una base caída: el validador no la necesita.
    file = "customers/customers_2026-01-01.csv"
    response = _validate(client, "customers", (SAMPLES / file).read_bytes())
    assert response.status_code == 200
    body = response.json()
    assert {q["record_key"] for q in body["quarantined"]} == _manifest_keys(file, ROW_DEFECTS)
    assert body["duplicates_dropped"] == len(_manifest_keys(file, {"duplicate_in_file"}))
    assert body["rejected_reason"] is None
    email = next(q for q in body["quarantined"] if q["record_key"] == "C00009")
    assert [e["field"] for e in email["errors"]] == ["email"]


def test_preview_quarantines_the_annotated_order_defects(client: TestClient) -> None:
    file = "orders/orders_batch_002.json"
    body = _validate(client, "orders", (SAMPLES / file).read_bytes()).json()
    assert {q["record_key"] for q in body["quarantined"]} == _manifest_keys(file, ROW_DEFECTS)
    assert body["rows_valid"] + body["rows_quarantined"] <= body["rows_read"]


def test_preview_requires_a_read_key(client: TestClient) -> None:
    assert _validate(client, "customers", b"a,b\n1,2\n", key=WRONG_KEY).status_code == 401


def test_preview_rejects_oversized_and_empty_files(client: TestClient) -> None:
    big = _validate(client, "customers", b"x" * (MAX_UPLOAD_BYTES + 1))
    assert big.status_code == 413
    assert _validate(client, "customers", b"  \n").status_code == 400
    assert _validate(client, "products", b"a\n1\n").status_code == 422


@pytest.mark.parametrize(
    ("source", "body", "reason"),
    [
        ("customers", b"\xff\xfe\x00garbage", "CSV ilegible"),
        ("orders", b"{not json", "JSON ilegible"),
        ("orders", b"[" * 100_000, "JSON ilegible"),  # anidamiento extremo: sin error 500
        ("customers", b"name\nAna\n", "faltan columnas obligatorias"),
    ],
    ids=["bad-utf8", "bad-json", "deep-json", "no-required-columns"],
)
def test_preview_reports_unreadable_files_as_rejected(
    client: TestClient, source: str, body: bytes, reason: str
) -> None:
    response = _validate(client, source, body)
    assert response.status_code == 200
    assert reason in response.json()["rejected_reason"]


# ------------------------------------------------------------------ frescura
@pytest.mark.parametrize(
    ("age_h", "expected"),
    [(None, "never"), (0.5, "fresh"), (26.0, "fresh"), (26.1, "stale")],
)
def test_freshness_status_against_the_sla(age_h: float | None, expected: str) -> None:
    now = datetime(2026, 1, 3, 12, tzinfo=UTC)
    last = None if age_h is None else now - timedelta(hours=age_h)
    row = {"source_name": "orders_json", "last_success_at": last, "last_attempt_at": now,
           "last_status": "success", "checked_at": now}  # fmt: skip
    result = _freshness(row, 26.0)
    assert result["status"] == expected
    assert "checked_at" not in result
    assert result["age_hours"] == (None if age_h is None else round(age_h, 1))
