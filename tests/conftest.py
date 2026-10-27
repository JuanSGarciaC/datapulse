"""Fixtures compartidas.

Las pruebas de integración usan una base dedicada (``DATAPULSE_TEST_DB_NAME``,
por defecto ``datapulse_test``) que se reconstruye con las migraciones reales.
Si PostgreSQL no está disponible se omiten localmente, pero en CI (``CI=true``)
se consideran un fallo: así nunca se reporta verde sin haberlas ejecutado.
"""

from __future__ import annotations

import os

# Reintentos de tareas de Prefect sin espera en pruebas (se lee al importar los flujos).
os.environ.setdefault("DATAPULSE_FLOW_TASK_RETRY_DELAY_S", "0")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "WARNING")
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError

from datapulse.config import DatabaseSettings, get_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _test_db_settings() -> DatabaseSettings:
    env = os.environ
    password = env.get("DATAPULSE_TEST_DB_PASSWORD", env.get("DATAPULSE_DB_PASSWORD", ""))
    return DatabaseSettings(
        host=env.get("DATAPULSE_TEST_DB_HOST", "localhost"),
        port=int(env.get("DATAPULSE_TEST_DB_PORT", "5432")),
        name=env.get("DATAPULSE_TEST_DB_NAME", "datapulse_test"),
        user=env.get("DATAPULSE_TEST_DB_USER", "datapulse_admin"),
        password=SecretStr(password),
        connect_timeout_s=3,
    )


@pytest.fixture(scope="session")
def test_db() -> DatabaseSettings:
    return _test_db_settings()


@pytest.fixture(scope="session")
def admin_engine(test_db: DatabaseSettings) -> Iterator[Engine]:
    engine = create_engine(test_db.url(), future=True)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        if os.environ.get("CI", "").lower() == "true":
            pytest.fail(f"PostgreSQL de pruebas no disponible en CI: {exc.orig}")
        pytest.skip("PostgreSQL de pruebas no disponible (define DATAPULSE_TEST_DB_*)")
    yield engine
    engine.dispose()


def alembic_config(engine: Engine) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["url"] = engine.url
    return cfg


@pytest.fixture
def migrated_db(admin_engine: Engine) -> Engine:
    """Base limpia en ``head`` para cada prueba que la pida (aislamiento total)."""
    cfg = alembic_config(admin_engine)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return admin_engine
