"""API simulada con el contrato de DummyJSON + inyección de fallos.

Comportamiento **verificado contra la API real** (2026-10-27) y replicado aquí:

* ``GET /products?limit=&skip=`` → ``{products, total, skip, limit}``; ``limit`` por
  defecto 30; ``limit=0`` devuelve todo; ``limit`` de la respuesta = elementos enviados.
* Pedir más allá del final → lista vacía con ``limit=0`` (no es error).
* ``limit`` negativo → 400 ``{"message": "Invalid 'limit' - should be a positive number"}``.
* ``select``, ``sortBy``/``order`` y ``delay`` (0-5000 ms).
* ``GET /http/{status}/{message}`` → ``application/problem+json``.

Extensiones propias (no existen en DummyJSON) bajo ``/_mock``, protegidas con token:
fallos intermitentes, límite de peticiones con ``Retry-After`` y reloj del catálogo.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from datapulse.mock_source.catalog import Catalog
from datapulse.mock_source.faults import FaultConfig, FaultInjector

DEFAULT_LIMIT = 30
MAX_DELAY_MS = 5000
PROBLEM_JSON = "application/problem+json; charset=utf-8"


class MockSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATAPULSE_MOCK_", env_file=".env", extra="ignore")

    catalog_path: Path = Path("data/samples/products/products_catalog.json")
    # Sin token, los endpoints /_mock no existen (404): seguro por defecto.
    admin_token: SecretStr | None = None
    as_of: datetime | None = None
    faults: FaultConfig = FaultConfig()  # JSON en DATAPULSE_MOCK_FAULTS


class ClockUpdate(BaseModel):
    as_of: datetime | None


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"message": message})


def _problem(status: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    body = {
        "status": status,
        "title": message,
        "detail": message,
        "message": message,
        "type": "about:blank",
    }
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_JSON, headers=headers)


def _parse_non_negative(raw: str | None, name: str, default: int) -> int | JSONResponse:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return _bad_request(f"Invalid '{name}' - should be a positive number")
    if value < 0:
        return _bad_request(f"Invalid '{name}' - should be a positive number")
    return value


def _project(product: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
    if not fields:
        return product
    keep = {"id", *fields}
    return {k: v for k, v in product.items() if k in keep}


@dataclass(frozen=True, slots=True)
class ListQuery:
    limit: int
    skip: int
    delay_ms: int


def _validate_list_query(
    limit: str | None, skip: str | None, delay: str | None, order: str
) -> ListQuery | JSONResponse:
    """Valida como DummyJSON: 400 con ``{"message": ...}`` en vez del 422 de FastAPI."""
    parsed: dict[str, int] = {}
    for name, raw, default in (
        ("limit", limit, DEFAULT_LIMIT),
        ("skip", skip, 0),
        ("delay", delay, 0),
    ):
        value = _parse_non_negative(raw, name, default)
        if isinstance(value, JSONResponse):
            return value
        parsed[name] = value
    if parsed["delay"] > MAX_DELAY_MS:
        return _bad_request(f"Invalid 'delay' - should be between 0 and {MAX_DELAY_MS}")
    if order not in {"asc", "desc"}:
        return _bad_request("Invalid 'order' - should be 'asc' or 'desc'")
    return ListQuery(limit=parsed["limit"], skip=parsed["skip"], delay_ms=parsed["delay"])


def _sorted(items: list[dict[str, Any]], sort_by: str | None, order: str) -> list[dict[str, Any]]:
    if not sort_by:
        return items
    present = [p for p in items if p.get(sort_by) is not None]
    missing = [p for p in items if p.get(sort_by) is None]
    present.sort(key=lambda p: p[sort_by], reverse=order == "desc")
    return present + missing  # nulos al final (como NULLS LAST)


def create_app(settings: MockSettings | None = None, catalog: Catalog | None = None) -> FastAPI:
    settings = settings or MockSettings()
    app = FastAPI(
        title="DataPulse mock source",
        description="Réplica del contrato de productos de DummyJSON con fallos inyectables.",
        version="1.0.0",
    )
    app.state.catalog = catalog or Catalog.from_file(settings.catalog_path)
    app.state.injector = FaultInjector(settings.faults)
    app.state.as_of = settings.as_of

    def require_admin(x_mock_admin_token: Annotated[str | None, Header()] = None) -> None:
        expected = settings.admin_token
        if expected is None:
            raise HTTPException(status_code=404)
        given = (x_mock_admin_token or "").encode()
        if not secrets.compare_digest(given, expected.get_secret_value().encode()):
            raise HTTPException(status_code=401, detail="token de administración inválido")

    def maybe_fault(skip: int | None) -> JSONResponse | None:
        injector: FaultInjector = app.state.injector
        fault = injector.check(skip)
        if fault is None:
            return None
        headers = injector.rate_headers()
        if fault.retry_after_s is not None:
            headers["Retry-After"] = str(fault.retry_after_s)
        message = "Too Many Requests" if fault.status == 429 else "Simulated upstream failure"  # noqa: PLR2004
        return _problem(fault.status, message, headers)

    async def simulated_latency(delay_ms: int) -> None:
        total = delay_ms + app.state.injector.config.latency_ms
        if total:
            await asyncio.sleep(total / 1000)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, Any]:
        return {"status": "ok", "products": len(app.state.catalog.snapshot(app.state.as_of))}

    @app.get("/products", tags=["products"])
    async def list_products(
        *,
        limit: str | None = None,
        skip: str | None = None,
        select: Annotated[list[str] | None, Query()] = None,
        sort_by: Annotated[str | None, Query(alias="sortBy")] = None,
        order: str = "asc",
        delay: str | None = None,
    ) -> JSONResponse:
        query = _validate_list_query(limit, skip, delay, order)
        if isinstance(query, JSONResponse):
            return query
        await simulated_latency(query.delay_ms)
        if (fault := maybe_fault(query.skip)) is not None:
            return fault

        items = _sorted(app.state.catalog.snapshot(app.state.as_of), sort_by, order)
        fields = [f.strip() for raw in (select or []) for f in raw.split(",") if f.strip()]
        end = None if query.limit == 0 else query.skip + query.limit
        page = items[query.skip : end]
        body = {
            "products": [_project(p, fields) for p in page],
            "total": len(items),
            "skip": query.skip,
            "limit": len(page),
        }
        return JSONResponse(content=body, headers=app.state.injector.rate_headers())

    @app.get("/products/{product_id}", tags=["products"])
    async def get_product(product_id: int) -> JSONResponse:
        if (fault := maybe_fault(None)) is not None:
            return fault
        for product in app.state.catalog.snapshot(app.state.as_of):
            if product["id"] == product_id:
                return JSONResponse(content=product)
        return JSONResponse(
            status_code=404, content={"message": f"Product with id '{product_id}' not found"}
        )

    @app.get("/http/{status}", tags=["mock-http"])
    @app.get("/http/{status}/{message}", tags=["mock-http"])
    def mock_http(status: int, message: str | None = None) -> JSONResponse:
        if not 100 <= status <= 599:  # noqa: PLR2004
            return _bad_request("Invalid status code")
        return _problem(status, message or "Simulated status")

    # ------------------------------------------------------------------ administración
    admin = [Depends(require_admin)]

    @app.get("/_mock/state", dependencies=admin, tags=["mock-admin"])
    def get_state() -> dict[str, Any]:
        injector: FaultInjector = app.state.injector
        return {
            "faults": injector.config.model_dump(),
            "as_of": app.state.as_of,
            "requests_seen": injector.requests_seen,
            "faults_injected": injector.faults_injected,
        }

    @app.put("/_mock/faults", dependencies=admin, tags=["mock-admin"])
    def set_faults(config: FaultConfig) -> dict[str, Any]:
        app.state.injector.configure(config)  # reinicia contadores y RNG
        return {"faults": config.model_dump()}

    @app.put("/_mock/clock", dependencies=admin, tags=["mock-admin"])
    def set_clock(update: ClockUpdate) -> dict[str, Any]:
        app.state.as_of = update.as_of
        return {"as_of": update.as_of, "products": len(app.state.catalog.snapshot(update.as_of))}

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        # Nunca se devuelve la traza al cliente.
        return _problem(500, "Internal error")

    return app
