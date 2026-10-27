"""Creación del engine de SQLAlchemy y comprobación de conectividad."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from datapulse.config import DatabaseSettings

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    """La base de datos no responde. El mensaje nunca incluye la contraseña."""


def build_engine(db: DatabaseSettings) -> Engine:
    return create_engine(
        db.url(),
        pool_size=db.pool_size,
        pool_pre_ping=True,  # descarta conexiones muertas tras reinicios de Postgres
        future=True,
    )


def check_connection(engine: Engine) -> str:
    """Devuelve la versión del servidor o lanza ``DatabaseUnavailableError``."""
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar_one()
    except SQLAlchemyError as exc:
        # ``hide_password=True`` evita filtrar credenciales en el mensaje de error.
        target = engine.url.render_as_string(hide_password=True)
        logger.error("database_unavailable", extra={"target": target})
        raise DatabaseUnavailableError(f"No se pudo conectar a {target}") from exc
    return str(version)
