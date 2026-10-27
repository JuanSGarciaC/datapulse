"""Entorno de Alembic: URL desde variables de entorno y metadata de DataPulse."""

from __future__ import annotations

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, create_engine

from datapulse.config import DatabaseSettings
from datapulse.db.schema import OPS, RAW, metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic solo gestiona los esquemas operativos/de entrada. staging y marts son de dbt.
MANAGED_SCHEMAS = {OPS, RAW}


def _include_name(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    return True


def _url() -> Any:
    # Las pruebas pueden inyectar una URL concreta vía config.attributes.
    return config.attributes.get("url") or DatabaseSettings().url()


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        include_schemas=True,
        include_name=_include_name,
        version_table_schema="public",
        compare_type=True,
        transaction_per_migration=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_url().render_as_string(hide_password=False),
        target_metadata=metadata,
        literal_binds=True,
        include_schemas=True,
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        _configure(conn)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
