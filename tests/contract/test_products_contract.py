"""Una sola suite de contrato, dos implementaciones: la API simulada y DummyJSON real.

Si DummyJSON cambia su contrato, la variante ``live`` falla en CI y sabemos que la
simulación (y el extractor que se construirá sobre ella) dejaron de ser fieles.
La variante ``mock`` corre siempre, sin red.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any, Protocol

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from datapulse.mock_source.app import MockSettings, create_app
from datapulse.sources.contracts import ErrorBody, RawProduct, parse_page
from tests.conftest import ROOT

DUMMYJSON_URL = os.environ.get("DATAPULSE_DUMMYJSON_URL", "https://dummyjson.com")
CATALOG = ROOT / "data/samples/products/products_catalog.json"


class HttpClient(Protocol):
    def get(self, url: str, *, params: dict[str, Any] | None = ...) -> httpx.Response: ...


@pytest.fixture(
    scope="module",
    params=["mock", pytest.param("dummyjson", marks=pytest.mark.live)],
)
def client(request: pytest.FixtureRequest) -> Iterator[HttpClient]:
    if request.param == "mock":
        app = create_app(MockSettings(_env_file=None, catalog_path=CATALOG))
        with TestClient(app) as tc:
            yield tc
        return
    with httpx.Client(base_url=DUMMYJSON_URL, timeout=30.0) as real:
        try:
            real.get("/test")
        except httpx.HTTPError as exc:
            if os.environ.get("CI", "").lower() == "true":
                pytest.fail(f"DummyJSON no responde desde CI: {exc}")
            pytest.skip(f"sin acceso a {DUMMYJSON_URL}: {exc}")
        yield real


def test_default_page_has_30_items_and_valid_envelope(client: HttpClient) -> None:
    page = parse_page(client.get("/products").json())
    assert page.skip == 0
    assert page.limit == len(page.products) == 30
    assert page.total > 30


def test_paginating_visits_every_product_exactly_once(client: HttpClient) -> None:
    seen: list[int] = []
    skip, total = 0, None
    while total is None or skip < total:
        page = parse_page(
            client.get("/products", params={"limit": 50, "skip": skip, "select": "id,meta"}).json()
        )
        total = page.total
        if not page.products:
            break
        seen.extend(p.id for p in page.products)
        skip += page.limit
    assert len(seen) == total
    assert len(set(seen)) == total


def test_limit_zero_returns_everything(client: HttpClient) -> None:
    page = parse_page(client.get("/products", params={"limit": 0, "select": "id,meta"}).json())
    assert len(page.products) == page.total


def test_beyond_the_end_is_empty_not_an_error(client: HttpClient) -> None:
    response = client.get("/products", params={"limit": 10, "skip": 100_000})
    assert response.status_code == 200
    page = parse_page(response.json())
    assert page.products == []
    assert page.limit == 0


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"limit": -1}, "Invalid 'limit' - should be a positive number"),
        ({"limit": "abc"}, "Invalid 'limit' - should be a positive number"),
        ({"skip": -1}, "Invalid 'skip' - should be a positive number"),
    ],
)
def test_invalid_paging_is_a_400_with_exact_message(
    client: HttpClient, params: dict[str, Any], expected: str
) -> None:
    response = client.get("/products", params=params)
    assert response.status_code == 400
    assert ErrorBody.model_validate(response.json()).message == expected


def test_select_projects_fields(client: HttpClient) -> None:
    body = client.get("/products", params={"limit": 3, "select": "title,price"}).json()
    for product in body["products"]:
        assert set(product) <= {"id", "title", "price"}
        assert "id" in product


def test_sort_desc_by_id(client: HttpClient) -> None:
    body = client.get(
        "/products", params={"limit": 5, "sortBy": "id", "order": "desc", "select": "id"}
    ).json()
    ids = [p["id"] for p in body["products"]]
    assert ids == sorted(ids, reverse=True)


def test_single_product_has_watermark_field(client: HttpClient) -> None:
    product = RawProduct.model_validate(client.get("/products/1").json())
    assert product.id == 1
    assert product.meta.updated_at.tzinfo is not None


def test_unknown_product_is_404_with_message(client: HttpClient) -> None:
    response = client.get("/products/987654")
    assert response.status_code == 404
    ErrorBody.model_validate(response.json())


def test_simulated_http_status_uses_problem_json(client: HttpClient) -> None:
    response = client.get("/http/503/contract-check")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["message"] == "contract-check"


def test_delay_parameter_adds_latency(client: HttpClient) -> None:
    started = time.perf_counter()
    response = client.get("/products", params={"limit": 1, "delay": 300, "select": "id"})
    assert response.status_code == 200
    assert time.perf_counter() - started >= 0.3
