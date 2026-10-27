"""Mínimo privilegio verificado contra el catálogo real de PostgreSQL."""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text

from datapulse.config import DatabaseSettings
from datapulse.db.bootstrap import ServiceUser, ensure_user
from datapulse.db.bootstrap import main as bootstrap_main
from datapulse.db.engine import build_engine, check_connection

pytestmark = pytest.mark.integration


def _has_table_priv(engine: Engine, role: str, table: str, priv: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT has_table_privilege(:r, :t, :p)"), {"r": role, "t": table, "p": priv}
            ).scalar_one()
        )


def _has_schema_priv(engine: Engine, role: str, schema: str, priv: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT has_schema_privilege(:r, :s, :p)"), {"r": role, "s": schema, "p": priv}
            ).scalar_one()
        )


def test_reader_can_only_read_ops(migrated_db: Engine) -> None:
    assert _has_table_priv(migrated_db, "dp_reader", "ops.pipeline_runs", "SELECT")
    for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        assert not _has_table_priv(migrated_db, "dp_reader", "ops.pipeline_runs", priv)


def test_reader_cannot_see_raw_or_staging(migrated_db: Engine) -> None:
    assert not _has_schema_priv(migrated_db, "dp_reader", "raw", "USAGE")
    assert not _has_schema_priv(migrated_db, "dp_reader", "staging", "USAGE")


def test_pipeline_writes_ops_but_cannot_delete(migrated_db: Engine) -> None:
    for priv in ("SELECT", "INSERT", "UPDATE"):
        assert _has_table_priv(migrated_db, "dp_pipeline", "ops.pipeline_runs", priv)
    assert not _has_table_priv(migrated_db, "dp_pipeline", "ops.pipeline_runs", "DELETE")
    assert not _has_schema_priv(migrated_db, "dp_pipeline", "marts", "USAGE")


def test_future_raw_tables_are_append_only_for_pipeline(migrated_db: Engine) -> None:
    # Simula una migración futura que crea una tabla en raw (como harán las de la Fase 3).
    with migrated_db.begin() as conn:
        conn.execute(text("CREATE TABLE raw.future_source (id int)"))
    assert _has_table_priv(migrated_db, "dp_pipeline", "raw.future_source", "INSERT")
    assert not _has_table_priv(migrated_db, "dp_pipeline", "raw.future_source", "UPDATE")
    assert not _has_table_priv(migrated_db, "dp_pipeline", "raw.future_source", "DELETE")
    assert _has_table_priv(migrated_db, "dp_transform", "raw.future_source", "SELECT")
    assert not _has_table_priv(migrated_db, "dp_transform", "raw.future_source", "INSERT")


def test_marts_created_by_transform_are_readable_by_reader(migrated_db: Engine) -> None:
    # dbt se ejecuta con rol dp_transform: sus tablas deben ser legibles por la API.
    with migrated_db.begin() as conn:
        conn.execute(text("SET LOCAL ROLE dp_transform"))
        conn.execute(text("CREATE TABLE marts.example_kpi (v int)"))
    assert _has_table_priv(migrated_db, "dp_reader", "marts.example_kpi", "SELECT")
    assert not _has_table_priv(migrated_db, "dp_reader", "marts.example_kpi", "INSERT")


def test_bootstrap_creates_login_user_idempotently_with_default_group_role(
    migrated_db: Engine,
) -> None:
    username = f"dp_it_{uuid.uuid4().hex[:8]}"
    # Contraseña con comillas y backslash: verifica el escapado de quote_literal.
    user = ServiceUser(username, "dp_reader", SecretStr("it's-a \\ tricky 'pwd'"))
    try:
        with migrated_db.begin() as conn:
            ensure_user(conn, user)
        with migrated_db.begin() as conn:
            ensure_user(conn, user)  # segunda vez: no falla y actualiza la contraseña

        login_url = migrated_db.url.set(
            username=username, password=user.password.get_secret_value()
        )
        login_engine = create_engine(login_url)
        with login_engine.connect() as conn:
            current = conn.execute(text("SELECT current_user")).scalar_one()
            assert current == "dp_reader"
            conn.execute(text("SELECT count(*) FROM ops.pipeline_runs"))
        login_engine.dispose()
    finally:
        with migrated_db.begin() as conn:
            conn.execute(text(f'DROP ROLE IF EXISTS "{username}"'))


def test_bootstrap_main_end_to_end(
    migrated_db: Engine, test_db: DatabaseSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ejecuta el comando real de bootstrap y verifica que cada servicio entra con su rol."""
    monkeypatch.setenv("DATAPULSE_DB_HOST", test_db.host)
    monkeypatch.setenv("DATAPULSE_DB_PORT", str(test_db.port))
    monkeypatch.setenv("DATAPULSE_DB_NAME", test_db.name)
    monkeypatch.setenv("DATAPULSE_DB_USER", test_db.user)
    monkeypatch.setenv("DATAPULSE_DB_PASSWORD", test_db.password.get_secret_value())
    passwords = {"pipeline": "pipe-pwd-1", "dbt": "dbt-pwd-1", "api": "api-pwd-1"}
    for svc, pwd in passwords.items():
        monkeypatch.setenv(f"DATAPULSE_{svc.upper()}_DB_PASSWORD", pwd)
    # Prefijo propio: la prueba no cambia las contraseñas de los usuarios reales de la app
    # (los roles son globales del clúster y la base de pruebas comparte servidor).
    prefix = f"dp_it_{uuid.uuid4().hex[:6]}_"
    monkeypatch.setenv("DATAPULSE_SERVICE_USER_PREFIX", prefix)

    try:
        assert bootstrap_main() == 0
        assert bootstrap_main() == 0  # idempotente
        _assert_service_logins(migrated_db, test_db, prefix, passwords)
    finally:
        with migrated_db.begin() as conn:
            for svc in passwords:
                conn.execute(text(f'DROP ROLE IF EXISTS "{prefix}{svc}"'))


def _assert_service_logins(
    migrated_db: Engine, test_db: DatabaseSettings, prefix: str, passwords: dict[str, str]
) -> None:
    expected = {"pipeline": "dp_pipeline", "dbt": "dp_transform", "api": "dp_reader"}
    for svc, group in expected.items():
        url = migrated_db.url.set(username=f"{prefix}{svc}", password=passwords[svc])
        engine = build_engine(
            DatabaseSettings.model_validate(
                {
                    "host": test_db.host,
                    "port": test_db.port,
                    "name": test_db.name,
                    "user": url.username,
                    "password": passwords[svc],
                }
            )
        )
        assert check_connection(engine)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT current_user")).scalar_one() == group
        engine.dispose()
