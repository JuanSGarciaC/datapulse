"""API simulada: extensiones propias (fallos, rate limit, reloj, admin)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from datapulse.mock_source.app import MockSettings, create_app
from datapulse.mock_source.catalog import Catalog
from datapulse.mock_source.faults import FaultConfig, FaultInjector
from tests.conftest import ROOT

CATALOG = ROOT / "data/samples/products/products_catalog.json"
ADMIN = {"X-Mock-Admin-Token": "s3cret-admin"}


def _client(**overrides: object) -> TestClient:
    settings = MockSettings(
        _env_file=None,
        catalog_path=CATALOG,
        admin_token=SecretStr("s3cret-admin"),
        **overrides,  # type: ignore[arg-type]
    )
    return TestClient(create_app(settings))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _client() as tc:
        yield tc


# ------------------------------------------------------------------ administración
def test_admin_disabled_without_token() -> None:
    settings = MockSettings(_env_file=None, catalog_path=CATALOG)
    with TestClient(create_app(settings)) as tc:
        assert tc.get("/_mock/state").status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Mock-Admin-Token": "wrong"}])
def test_admin_rejects_missing_or_wrong_token(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/_mock/state", headers=headers).status_code == 401


def test_admin_rejects_invalid_fault_config(client: TestClient) -> None:
    response = client.put("/_mock/faults", headers=ADMIN, json={"fail_rate": 2})
    assert response.status_code == 422


# ------------------------------------------------------------------ fallos
def test_fail_offsets_fail_exactly_n_times_then_recover(client: TestClient) -> None:
    client.put("/_mock/faults", headers=ADMIN, json={"fail_offsets": {"30": 2}, "fail_status": 502})
    statuses = [client.get("/products", params={"skip": 30}).status_code for _ in range(3)]
    assert statuses == [502, 502, 200]
    assert client.get("/products", params={"skip": 0}).status_code == 200
    state = client.get("/_mock/state", headers=ADMIN).json()
    assert state["faults_injected"] == 2
    assert state["requests_seen"] == 4


def test_fault_response_is_problem_json(client: TestClient) -> None:
    client.put("/_mock/faults", headers=ADMIN, json={"fail_rate": 1.0})
    response = client.get("/products/1")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")


def test_fail_rate_is_deterministic_for_a_seed() -> None:
    def sequence() -> list[bool]:
        injector = FaultInjector(FaultConfig(fail_rate=0.3, seed=11))
        return [injector.check(None) is not None for _ in range(200)]

    first = sequence()
    assert first == sequence()
    assert 30 < sum(first) < 90  # ~30 % de 200


def test_rate_limit_returns_429_with_retry_after_and_resets_with_window() -> None:
    now = [1000.0]
    injector = FaultInjector(FaultConfig(rate_limit=2, rate_window_s=10), clock=lambda: now[0])
    assert injector.check(0) is None
    assert injector.check(0) is None
    now[0] += 4
    fault = injector.check(0)
    assert fault is not None
    assert fault.status == 429
    assert fault.retry_after_s == 6
    assert injector.rate_headers() == {"x-ratelimit-limit": "2", "x-ratelimit-remaining": "0"}
    now[0] += 6  # nueva ventana
    assert injector.check(0) is None


def test_rate_limit_headers_over_http(client: TestClient) -> None:
    client.put("/_mock/faults", headers=ADMIN, json={"rate_limit": 1})
    ok = client.get("/products", params={"limit": 1})
    assert ok.headers["x-ratelimit-limit"] == "1"
    limited = client.get("/products", params={"limit": 1})
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_reconfiguring_resets_counters(client: TestClient) -> None:
    client.put("/_mock/faults", headers=ADMIN, json={"fail_offsets": {"0": 1}})
    assert client.get("/products").status_code == 503
    client.put("/_mock/faults", headers=ADMIN, json={"fail_offsets": {"0": 1}})
    assert client.get("/products").status_code == 503  # el contador volvió a cero


def test_latency_from_config_is_applied() -> None:
    with _client(faults=FaultConfig(latency_ms=50)) as tc:
        started = time.perf_counter()
        tc.get("/products", params={"limit": 1})
        assert time.perf_counter() - started >= 0.05


@pytest.mark.parametrize(
    ("params", "fragment"),
    [({"delay": 5001}, "delay"), ({"order": "sideways"}, "order"), ({"delay": "x"}, "delay")],
)
def test_other_invalid_params_are_400(
    client: TestClient, params: dict[str, str | int], fragment: str
) -> None:
    response = client.get("/products", params=params)
    assert response.status_code == 400
    assert fragment in response.json()["message"]


def test_http_endpoint_rejects_invalid_status(client: TestClient) -> None:
    assert client.get("/http/42").status_code == 400
    assert client.get("/http/418").json()["message"] == "Simulated status"


# ------------------------------------------------------------------ catálogo y reloj
def test_clock_controls_which_versions_are_visible(client: TestClient) -> None:
    catalog = Catalog.from_file(CATALOG)
    before = {p["id"]: p for p in catalog.snapshot(datetime(2025, 12, 31, tzinfo=UTC))}
    after = {p["id"]: p for p in catalog.snapshot(None)}
    changed = [
        pid for pid in before if before[pid]["meta"]["updatedAt"] != after[pid]["meta"]["updatedAt"]
    ]
    assert changed, "el catálogo debe tener actualizaciones posteriores al 31-dic"
    pid = changed[0]

    client.put("/_mock/clock", headers=ADMIN, json={"as_of": "2025-12-31T23:59:59Z"})
    assert client.get("/health").json()["products"] == len(before)
    assert (
        client.get(f"/products/{pid}").json()["meta"]["updatedAt"]
        == before[pid]["meta"]["updatedAt"]
    )

    client.put("/_mock/clock", headers=ADMIN, json={"as_of": None})
    assert client.get("/health").json()["products"] == len(after)
    assert (
        client.get(f"/products/{pid}").json()["meta"]["updatedAt"]
        == after[pid]["meta"]["updatedAt"]
    )


def test_sort_puts_nulls_last(client: TestClient) -> None:
    body = client.get("/products", params={"limit": 0, "sortBy": "price", "select": "price"}).json()
    prices = [p.get("price") for p in body["products"]]
    non_null = [p for p in prices if p is not None]
    assert non_null == sorted(non_null)
    assert prices[len(non_null) :] == [None] * (len(prices) - len(non_null))


def test_empty_catalog_is_rejected() -> None:
    with pytest.raises(ValueError, match="vacío"):
        Catalog([])


def test_fault_config_validation() -> None:
    with pytest.raises(ValidationError):
        FaultConfig.model_validate({"fail_status": 404})
    with pytest.raises(ValidationError):
        FaultConfig.model_validate({"unknown": 1})


def test_entrypoint_reads_host_and_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from datapulse.mock_source import __main__ as entry  # noqa: PLC0415 - import diferido a propósito

    captured: dict[str, object] = {}
    monkeypatch.setenv("DATAPULSE_MOCK_HOST", "0.0.0.0")  # noqa: S104 - valor de prueba
    monkeypatch.setenv("DATAPULSE_MOCK_PORT", "9999")
    monkeypatch.setenv("DATAPULSE_MOCK_CATALOG_PATH", str(CATALOG))
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: captured.update(kw))
    entry.main()
    assert captured["host"] == "0.0.0.0"  # noqa: S104
    assert captured["port"] == 9999
