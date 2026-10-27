"""Autenticación por API key en cabecera ``X-API-Key``.

* Comparación en tiempo constante (``hmac.compare_digest``) contra el hash de
  cada clave configurada: el tiempo de respuesta no revela cuánto coincide.
* La clave nunca se registra en logs ni en mensajes de error.
* Sin claves configuradas, la API rechaza todo (falla cerrada, no abierta).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterable
from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from pydantic import SecretStr

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False, description="Clave de acceso")
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="API key ausente o inválida",
    headers={"WWW-Authenticate": "APIKey"},
)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


class KeyChecker:
    def __init__(self, keys: Iterable[SecretStr]) -> None:
        self._digests = [_digest(k.get_secret_value()) for k in keys]

    def __bool__(self) -> bool:
        return bool(self._digests)

    def valid(self, candidate: str | None) -> bool:
        if not candidate:
            return False
        given = _digest(candidate)
        # Recorre todas las claves siempre: el tiempo no depende de cuál coincide.
        matches = [hmac.compare_digest(given, d) for d in self._digests]
        return any(matches)


def require_key(checker_attr: str, *, hide_when_unconfigured: bool = False) -> Callable[..., None]:
    """Dependencia que valida ``X-API-Key`` contra ``app.state.<checker_attr>``.

    ``hide_when_unconfigured``: si no hay claves configuradas responde 404 (el endpoint
    "no existe"), útil para la administración. Si no, 401 (falla cerrada).
    """

    def dependency(
        request: Request, api_key: Annotated[str | None, Security(API_KEY_HEADER)] = None
    ) -> None:
        checker: KeyChecker = getattr(request.app.state, checker_attr)
        if not checker:
            if hide_when_unconfigured:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
            raise _UNAUTHORIZED
        if not checker.valid(api_key):
            raise _UNAUTHORIZED

    return dependency
