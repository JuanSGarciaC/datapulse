"""API sin base de datos: autenticación, validación, cabeceras, errores y disparo admin."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine

from datapulse.api.app import create_app
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
