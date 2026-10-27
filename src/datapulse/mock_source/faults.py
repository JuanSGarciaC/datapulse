"""Inyección de fallos determinista para la API simulada.

DummyJSON real permite simular latencia (``delay``) y un estado HTTP fijo
(``/http/{status}``), pero **no** fallos intermitentes a mitad de una
paginación ni un límite de peticiones con ``Retry-After``. Eso es lo que hace
falta para probar reintentos y recuperación, y lo añade este módulo.

Todo es determinista (RNG con semilla y contadores), para que las pruebas que
dependen de un fallo concreto no sean intermitentes.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

FailStatus = Literal[500, 502, 503, 504]


class FaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int = 0
    fail_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    fail_status: FailStatus = 503
    latency_ms: Annotated[int, Field(ge=0, le=5000)] = 0
    # Límite de peticiones por ventana (0 = desactivado). Responde 429 + Retry-After.
    rate_limit: Annotated[int, Field(ge=0, le=10_000)] = 0
    rate_window_s: Annotated[int, Field(ge=1, le=3600)] = 60
    # {skip: n}: las primeras ``n`` peticiones con ese ``skip`` fallan, luego funcionan.
    # Permite probar “falla la página 3 dos veces y se recupera” de forma exacta.
    fail_offsets: dict[Annotated[int, Field(ge=0)], Annotated[int, Field(ge=1, le=100)]] = {}


@dataclass(frozen=True, slots=True)
class Fault:
    status: int
    retry_after_s: int | None = None


class FaultInjector:
    """Estado compartido entre peticiones; protegido con un lock (uvicorn usa hilos)."""

    def __init__(self, config: FaultConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock  # inyectable: las pruebas controlan el tiempo sin dormir
        self.configure(config)

    def configure(self, config: FaultConfig) -> None:
        with self._lock:
            self.config = config
            self._rng = random.Random(config.seed)  # noqa: S311  # nosec B311 - simulación determinista, no criptografía
            self._offset_hits: dict[int, int] = {}
            self._window_start = self._clock()
            self._window_count = 0
            self.requests_seen = 0
            self.faults_injected = 0

    def check(self, skip: int | None) -> Fault | None:
        """Decide si esta petición falla. Orden: rate limit → offsets → aleatorio."""
        with self._lock:
            self.requests_seen += 1
            cfg = self.config
            if cfg.rate_limit:
                now = self._clock()
                if now - self._window_start >= cfg.rate_window_s:
                    self._window_start, self._window_count = now, 0
                self._window_count += 1
                if self._window_count > cfg.rate_limit:
                    self.faults_injected += 1
                    remaining = cfg.rate_window_s - (now - self._window_start)
                    return Fault(429, retry_after_s=max(1, int(remaining + 0.999)))
            if skip is not None and skip in cfg.fail_offsets:
                hits = self._offset_hits.get(skip, 0)
                if hits < cfg.fail_offsets[skip]:
                    self._offset_hits[skip] = hits + 1
                    self.faults_injected += 1
                    return Fault(cfg.fail_status)
            if cfg.fail_rate and self._rng.random() < cfg.fail_rate:
                self.faults_injected += 1
                return Fault(cfg.fail_status)
            return None

    def rate_headers(self) -> dict[str, str]:
        cfg = self.config
        if not cfg.rate_limit:
            return {}
        remaining = max(0, cfg.rate_limit - self._window_count)
        return {"x-ratelimit-limit": str(cfg.rate_limit), "x-ratelimit-remaining": str(remaining)}
