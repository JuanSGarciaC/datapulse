"""Cliente de la API de productos: paginación ``limit``/``skip`` con reintentos.

Política de reintentos (por petición, independiente de los reintentos de tarea
del orquestador):

* Se reintenta: errores de red/timeout, 429, 500, 502, 503 y 504.
* No se reintenta: cualquier otro 4xx o una respuesta que viola el contrato.
  Repetir no lo arreglaría y solo ocultaría el problema.
* Espera: ``Retry-After`` si la fuente lo envía; si no, backoff exponencial con
  *full jitter* (evita que muchos clientes reintenten a la vez).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

import httpx2 as httpx
from pydantic import ValidationError

from datapulse.sources.contracts import parse_page

logger = logging.getLogger(__name__)

RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})
MAX_PAGES: Final = 10_000  # salvaguarda ante una fuente que nunca termina


class SourceError(RuntimeError):
    """Fallo de la fuente que no se resuelve reintentando (o reintentos agotados)."""


class ContractViolationError(SourceError):
    """La respuesta no cumple el contrato de la API: hay que revisar la integración."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 5
    backoff_base_s: float = 0.5
    backoff_max_s: float = 30.0

    def delay(self, attempt: int, retry_after: str | None, rng: random.Random) -> float:
        if retry_after is not None:
            try:
                return min(float(retry_after), self.backoff_max_s)
            except ValueError:
                pass  # formato fecha HTTP: se usa el backoff normal
        ceiling = min(self.backoff_max_s, self.backoff_base_s * 2**attempt)
        return rng.uniform(0, ceiling)


@dataclass(slots=True)
class FetchStats:
    pages: int = 0
    requests: int = 0
    retries: int = 0
    total_reported: int | None = None
    total_changed: bool = False
    waits_s: list[float] = field(default_factory=list)


@dataclass(slots=True)
class FetchResult:
    records: list[dict[str, Any]]
    stats: FetchStats


class ProductsClient:
    def __init__(
        self,
        http: httpx.Client,
        *,
        page_size: int = 50,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not 1 <= page_size <= 1000:  # noqa: PLR2004
            raise ValueError("page_size debe estar entre 1 y 1000")
        self._http = http
        self._page_size = page_size
        self._policy = policy or RetryPolicy()
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311  # nosec B311 - jitter, no criptografía

    def _get(self, params: dict[str, int], stats: FetchStats) -> httpx.Response:
        last_error = "sin respuesta"
        for attempt in range(self._policy.max_retries + 1):
            stats.requests += 1
            retry_after: str | None = None
            try:
                response = self._http.get("/products", params=params)
            except httpx.TransportError as exc:  # red, DNS, timeout
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:  # noqa: PLR2004
                    return response
                if response.status_code not in RETRYABLE_STATUS:
                    raise SourceError(
                        f"HTTP {response.status_code} no reintentable en /products {params}"
                    )
                last_error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("retry-after")
            if attempt == self._policy.max_retries:
                break
            wait = self._policy.delay(attempt, retry_after, self._rng)
            stats.retries += 1
            stats.waits_s.append(wait)
            logger.warning(
                "source_retry",
                extra={
                    "params": params,
                    "attempt": attempt + 1,
                    "error": last_error,
                    "wait_s": round(wait, 3),
                },
            )
            self._sleep(wait)
        raise SourceError(f"reintentos agotados en /products {params}: {last_error}")

    def fetch_all(self) -> FetchResult:
        stats = FetchStats()
        records: list[dict[str, Any]] = []
        skip = 0
        while stats.pages < MAX_PAGES:
            response = self._get({"limit": self._page_size, "skip": skip}, stats)
            try:
                payload = response.json()
                page = parse_page(payload)
            except (ValidationError, ValueError) as exc:
                raise ContractViolationError(
                    f"página skip={skip} no cumple el contrato: {exc}"
                ) from exc
            stats.pages += 1
            if stats.total_reported is None:
                stats.total_reported = page.total
            elif page.total != stats.total_reported:
                # Con paginación por offset, si la fuente cambia durante la lectura se
                # pueden saltar o repetir elementos. Se avisa; la deduplicación lo absorbe.
                stats.total_changed = True
            if not page.products:
                break
            # Se guardan los dicts originales (fidelidad total); el contrato solo valida.
            records.extend(payload["products"])
            skip += page.limit
            if skip >= page.total:
                break
        else:  # pragma: no cover - salvaguarda
            raise SourceError(f"se alcanzó el máximo de {MAX_PAGES} páginas")
        return FetchResult(records=records, stats=stats)
