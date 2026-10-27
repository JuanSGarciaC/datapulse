"""Hashes de idempotencia y cliente REST (reintentos, Retry-After, contrato)."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from datapulse.ingestion.hashing import bytes_hash, canonical_json, record_hash, set_hash
from datapulse.ingestion.rest import (
    ContractViolationError,
    ProductsClient,
    RetryPolicy,
    SourceError,
)
from datapulse.mock_source.app import MockSettings, create_app
from datapulse.mock_source.faults import FaultConfig

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data/samples/products/products_catalog.json"


# ------------------------------------------------------------------ hashing
def test_canonical_json_ignores_key_order_and_normalizes_values() -> None:
    a = {"b": Decimal("1.50"), "a": datetime(2026, 1, 1, tzinfo=UTC)}
    b = {"a": datetime(2026, 1, 1, tzinfo=UTC), "b": Decimal("1.5")}
    assert canonical_json(a) == canonical_json(b)
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


def test_record_hash_ignores_technical_columns_but_not_extra() -> None:
    base = {"id": 1, "_row_number": 5, "_extra": {}}
    assert record_hash(base) == record_hash(base | {"_row_number": 99})
    assert record_hash(base) != record_hash(base | {"_extra": {"tier": "gold"}})
    assert record_hash(base) != record_hash(base | {"id": 2})


def test_set_hash_is_order_independent_and_rejects_content() -> None:
    h1, h2 = bytes_hash(b"a"), bytes_hash(b"b")
    assert set_hash([h1, h2]) == set_hash([h2, h1])
    with pytest.raises(ValueError, match="hashes"):
        set_hash(["producto sintético"])


# ------------------------------------------------------------------ cliente REST
@pytest.fixture
def mock_http() -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(faults: FaultConfig | None = None) -> TestClient:
        settings = MockSettings(
            _env_file=None, catalog_path=CATALOG, faults=faults or FaultConfig()
        )
        client = TestClient(create_app(settings))
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


def _client(http: httpx.Client, waits: list[float], **kw: object) -> ProductsClient:
    return ProductsClient(http, sleep=waits.append, rng=random.Random(0), **kw)  # type: ignore[arg-type]


def test_reads_every_page(mock_http: Callable[..., TestClient]) -> None:
    waits: list[float] = []
    result = _client(mock_http(), waits, page_size=64).fetch_all()
    assert len(result.records) == 200
    assert len({r["id"] for r in result.records}) == 200
    assert result.stats.pages == 4
    assert result.stats.retries == 0
    assert "meta" in result.records[0]  # dicts originales, no re-serializados


def test_retries_transient_failures_then_recovers(mock_http: Callable[..., TestClient]) -> None:
    waits: list[float] = []
    http = mock_http(FaultConfig(fail_offsets={50: 2}, fail_status=502))
    result = _client(http, waits).fetch_all()
    assert len(result.records) == 200
    assert result.stats.retries == 2
    assert len(waits) == 2
    assert all(0 <= w <= 30 for w in waits)


def test_respects_retry_after(mock_http: Callable[..., TestClient]) -> None:
    waits: list[float] = []
    http = mock_http(FaultConfig(rate_limit=2, rate_window_s=7))
    with pytest.raises(SourceError, match="reintentos agotados"):
        _client(http, waits, policy=RetryPolicy(max_retries=2)).fetch_all()
    assert waits
    assert all(w == 7 for w in waits)  # el valor exacto de Retry-After


def test_gives_up_after_max_retries(mock_http: Callable[..., TestClient]) -> None:
    waits: list[float] = []
    http = mock_http(FaultConfig(fail_offsets={0: 50}))
    with pytest.raises(SourceError, match="HTTP 503"):
        _client(http, waits, policy=RetryPolicy(max_retries=3)).fetch_all()
    assert len(waits) == 3


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))


def test_non_retryable_status_fails_fast() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, json={"message": "nope"})

    with pytest.raises(SourceError, match="no reintentable"):
        _client(_transport(handler), []).fetch_all()
    assert len(calls) == 1


def test_contract_violation_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    with pytest.raises(ContractViolationError):
        _client(_transport(handler), []).fetch_all()


def test_network_errors_are_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("caída simulada", request=request)
        return httpx.Response(200, json={"products": [], "total": 0, "skip": 0, "limit": 0})

    waits: list[float] = []
    result = _client(_transport(handler), waits).fetch_all()
    assert result.records == []
    assert len(waits) == 2


def test_detects_total_changing_during_pagination() -> None:
    meta = {"createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}

    def handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params["skip"])
        total = 3 if skip == 0 else 4
        products = [{"id": skip + 1, "meta": meta}, {"id": skip + 2, "meta": meta}][: total - skip]
        return httpx.Response(
            200, json={"products": products, "total": total, "skip": skip, "limit": len(products)}
        )

    result = _client(_transport(handler), [], page_size=2).fetch_all()
    assert result.stats.total_changed
    assert [r["id"] for r in result.records] == [1, 2, 3, 4]


def test_policy_backoff_and_validation() -> None:
    policy = RetryPolicy(backoff_base_s=1, backoff_max_s=5)
    rng = random.Random(1)
    assert all(0 <= policy.delay(10, None, rng) <= 5 for _ in range(50))
    assert policy.delay(0, "120", rng) == 5  # Retry-After acotado
    assert 0 <= policy.delay(0, "Wed, 21 Oct 2026 07:28:00 GMT", rng) <= 1
    with pytest.raises(ValueError, match="page_size"):
        ProductsClient(_transport(lambda r: httpx.Response(200)), page_size=0)
