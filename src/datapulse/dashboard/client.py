"""Cliente de la API para el dashboard.

El dashboard **no** se conecta a PostgreSQL: consume la API como cualquier otro
cliente. Así valida que la API es suficiente y el dashboard no necesita
credenciales de base de datos.
"""

from __future__ import annotations

import contextlib
from datetime import date
from typing import Any

import httpx2 as httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DashboardSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATAPULSE_DASHBOARD_", env_file=".env", extra="ignore"
    )

    api_url: str = "http://127.0.0.1:8000"
    api_key: SecretStr | None = None
    # Opcional: sin ella el panel es de solo lectura y no ofrece disparar pipelines.
    admin_key: SecretStr | None = None
    # Solo para la demo: con las dos, el panel puede provocar fallos en la fuente simulada.
    mock_url: str | None = None
    mock_admin_token: SecretStr | None = None
    timeout_s: float = 10.0
    cache_ttl_s: int = 60


class ApiError(RuntimeError):
    """Error con un mensaje apto para mostrar al usuario (sin detalles internos)."""


class ApiClient:
    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | None,
        *,
        http: httpx.Client | None = None,
        timeout_s: float = 10.0,
        admin_key: SecretStr | None = None,
    ) -> None:
        headers = {"X-API-Key": api_key.get_secret_value()} if api_key else {}
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._headers = headers
        # Una variable vacía en .env llega como cadena vacía: cuenta como «sin clave».
        admin = admin_key.get_secret_value() if admin_key else ""
        self._admin_headers = {"X-API-Key": admin} if admin else None

    @property
    def can_trigger(self) -> bool:
        return self._admin_headers is not None

    @classmethod
    def from_settings(cls, settings: DashboardSettings | None = None) -> ApiClient:
        cfg = settings or DashboardSettings()
        return cls(cfg.api_url, cfg.api_key, timeout_s=cfg.timeout_s, admin_key=cfg.admin_key)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params, self._headers)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        headers: dict[str, str],
        content: bytes | None = None,
    ) -> Any:
        clean = {
            k: v.isoformat() if isinstance(v, date) else v
            for k, v in (params or {}).items()
            if v is not None
        }
        try:
            response = self._http.request(
                method, path, params=clean, headers=headers, content=content
            )
        except httpx.TransportError as exc:
            raise ApiError(
                "No se puede conectar con la API de DataPulse. ¿Está levantada?"
            ) from exc
        if response.status_code == 401:  # noqa: PLR2004
            raise ApiError(
                "La API rechazó la clave. Revisa DATAPULSE_DASHBOARD_API_KEY o, al disparar "
                "un flujo, DATAPULSE_DASHBOARD_ADMIN_KEY."
            )
        if response.status_code >= 400:  # noqa: PLR2004
            detail = ""
            with contextlib.suppress(ValueError):
                detail = str(response.json().get("detail", ""))[:200]
            raise ApiError(f"La API respondió {response.status_code}. {detail}".strip())
        return response.json()

    def _all_pages(
        self, path: str, params: dict[str, Any], page_size: int = 500, max_items: int = 20_000
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._get(path, params | {"limit": page_size, "offset": offset})
            items.extend(page["items"])
            offset += page_size
            if offset >= page["total"] or not page["items"] or len(items) >= max_items:
                return items

    # -------------------------------------------------------------- consultas
    def ready(self) -> dict[str, Any]:
        try:
            response = self._http.get("/health/ready")
        except httpx.TransportError:
            return {"status": "unreachable", "database": None}
        result: dict[str, Any] = response.json()
        return result

    def kpis(self, date_from: date | None = None, date_to: date | None = None) -> dict[str, Any]:
        result: dict[str, Any] = self._get(
            "/v1/kpis/summary", {"date_from": date_from, "date_to": date_to}
        )
        return result

    def daily_sales(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._all_pages(
            "/v1/sales/daily", {"date_from": date_from, "date_to": date_to, "category": category}
        )

    def sales_by_category(
        self, date_from: date | None = None, date_to: date | None = None
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get(
            "/v1/sales/by-category", {"date_from": date_from, "date_to": date_to}
        )
        return result

    def top_products(
        self,
        limit: int = 10,
        date_from: date | None = None,
        date_to: date | None = None,
        metric: str = "revenue",
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get(
            "/v1/products/top",
            {"limit": limit, "date_from": date_from, "date_to": date_to, "metric": metric},
        )
        return result

    def pipeline_runs(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get(
            "/v1/pipeline-runs", {"limit": limit, "status": status}
        )["items"]
        return result

    def batches(self, limit: int = 100) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get("/v1/ingestion/batches", {"limit": limit})["items"]
        return result

    def trigger(self, name: str) -> dict[str, Any]:
        """Dispara un despliegue de la lista cerrada de la API (``daily`` o ``products``)."""
        if self._admin_headers is None:
            raise ApiError("El panel no tiene clave de administración: es de solo lectura.")
        result: dict[str, Any] = self._request(
            "POST", f"/v1/admin/pipelines/{name}/runs", None, self._admin_headers
        )
        return result

    def freshness(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get("/v1/quality/freshness")
        return result

    def order_lineage(self, order_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._get(f"/v1/lineage/orders/{order_id}")
        return result

    def quarantine(self) -> list[dict[str, Any]]:
        """Filas en cuarentena (la API ya quita nombres y enmascara el email)."""
        return self._all_pages("/v1/quality/quarantine", {}, max_items=2000)

    def validate_file(self, source: str, data: bytes) -> dict[str, Any]:
        """Prueba un archivo contra los contratos; la API no guarda nada."""
        result: dict[str, Any] = self._request(
            "POST", f"/v1/contracts/{source}/validate", None, self._headers, content=data
        )
        return result

    def run_detail(self, run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._get(f"/v1/pipeline-runs/{run_id}")
        return result

    def quality_summary(self, layer: str | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get("/v1/quality/summary", {"layer": layer})
        return result

    def failed_quality_results(self, limit: int = 200) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get(
            "/v1/quality/results", {"passed": "false", "limit": limit}
        )["items"]
        return result

    def volumes(self, days: int = 30) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get("/v1/metrics/volumes", {"days": days})
        return result


class SourceChaos:
    """Provoca fallos en la fuente simulada (``/_mock/faults``) para ver la resiliencia.

    Solo existe en la demo: sin URL o sin token de la fuente simulada, ``available`` es
    falso y el panel no muestra los botones.
    """

    # Seis 503 seguidos en la primera página: agotan los 5 reintentos HTTP del cliente
    # (RetryPolicy.max_retries); Prefect reintenta la tarea y la segunda vez entra.
    FAILURES = 6

    def __init__(
        self,
        base_url: str | None,
        token: SecretStr | None,
        *,
        http: httpx.Client | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        secret = token.get_secret_value() if token else ""
        self.available = bool(base_url and secret)
        self._headers = {"X-Mock-Admin-Token": secret}
        self._http = http or (
            httpx.Client(base_url=base_url, timeout=timeout_s) if base_url and secret else None
        )

    @classmethod
    def from_settings(cls, settings: DashboardSettings | None = None) -> SourceChaos:
        cfg = settings or DashboardSettings()
        return cls(cfg.mock_url, cfg.mock_admin_token, timeout_s=cfg.timeout_s)

    def _put(self, config: dict[str, Any]) -> None:
        if not self.available or self._http is None:
            raise ApiError("La demo de fallos no está configurada.")
        try:
            response = self._http.put("/_mock/faults", json=config, headers=self._headers)
        except httpx.TransportError as exc:
            raise ApiError("No se puede conectar con la fuente simulada.") from exc
        if response.status_code >= 400:  # noqa: PLR2004
            raise ApiError(f"La fuente simulada respondió {response.status_code}.")

    def break_source(self) -> None:
        self._put({"fail_offsets": {"0": self.FAILURES}})

    def restore(self) -> None:
        self._put({})
