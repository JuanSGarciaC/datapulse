"""Crea/actualiza los usuarios de login de cada componente.

Separación deliberada:

* Las **migraciones** (Alembic) crean los *roles de grupo* sin login
  (``dp_pipeline``, ``dp_transform``, ``dp_reader``) y sus permisos. No
  contienen secretos, por eso se pueden versionar.
* Este **bootstrap** crea los *usuarios de login* con contraseñas leídas de
  variables de entorno y los hace miembros del grupo correspondiente.

Cada usuario tiene ``SET role`` por defecto a su grupo: así los objetos que crea
dbt pertenecen a ``dp_transform`` y los privilegios por defecto se aplican
siempre, sin importar qué usuario concreto ejecutó dbt.

Uso: ``python -m datapulse.db.bootstrap`` (idempotente, se puede repetir).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Final

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Connection, text

from datapulse.config import Environment, get_settings, reject_placeholder_in_prod
from datapulse.db.engine import build_engine
from datapulse.observability.logging import configure_logging

logger = logging.getLogger(__name__)

MIN_PROD_PASSWORD_LENGTH: Final = 16


@dataclass(frozen=True, slots=True)
class ServiceUser:
    username: str
    group_role: str
    password: SecretStr


class ServicePasswords(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATAPULSE_", env_file=".env", extra="ignore")

    pipeline_db_password: SecretStr
    dbt_db_password: SecretStr
    api_db_password: SecretStr
    # Los roles son globales del clúster: un prefijo distinto permite crear usuarios de
    # prueba sin tocar los de la aplicación que corren en el mismo servidor.
    service_user_prefix: str = Field(default="datapulse_", pattern=r"^[a-z][a-z0-9_]{0,30}$")


def service_users(passwords: ServicePasswords) -> list[ServiceUser]:
    prefix = passwords.service_user_prefix
    return [
        ServiceUser(f"{prefix}pipeline", "dp_pipeline", passwords.pipeline_db_password),
        ServiceUser(f"{prefix}dbt", "dp_transform", passwords.dbt_db_password),
        ServiceUser(f"{prefix}api", "dp_reader", passwords.api_db_password),
    ]


def validate_password(user: ServiceUser, env: Environment) -> None:
    secret = user.password.get_secret_value()
    if not secret:
        raise ValueError(f"La contraseña de {user.username} está vacía")
    reject_placeholder_in_prod(f"La contraseña de {user.username}", secret, env)
    if env is Environment.PROD and len(secret) < MIN_PROD_PASSWORD_LENGTH:
        raise ValueError(
            f"En prod la contraseña de {user.username} debe tener "
            f">= {MIN_PROD_PASSWORD_LENGTH} caracteres"
        )


def _quote_ident(conn: Connection, ident: str) -> str:
    return str(conn.execute(text("SELECT quote_ident(:i)"), {"i": ident}).scalar_one())


def _quote_literal(conn: Connection, value: str) -> str:
    # Los comandos de utilidad (CREATE/ALTER ROLE) no aceptan parámetros enlazados en el
    # servidor: se escapa con la propia función de PostgreSQL, nunca con f-strings crudos.
    return str(conn.execute(text("SELECT quote_literal(:v)"), {"v": value}).scalar_one())


def ensure_user(conn: Connection, user: ServiceUser) -> None:
    ident = _quote_ident(conn, user.username)
    group = _quote_ident(conn, user.group_role)
    pwd = _quote_literal(conn, user.password.get_secret_value())
    exists = conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": user.username}
    ).first()
    verb = "ALTER" if exists else "CREATE"
    conn.execute(text(f"{verb} ROLE {ident} WITH LOGIN NOSUPERUSER NOCREATEROLE PASSWORD {pwd}"))
    conn.execute(text(f"GRANT {group} TO {ident}"))
    conn.execute(text(f"ALTER ROLE {ident} SET role = {group}"))
    logger.info("service_user_ready", extra={"user": user.username, "group": user.group_role})


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    users = service_users(ServicePasswords())  # valores leídos del entorno
    reject_placeholder_in_prod(
        "DATAPULSE_DB_PASSWORD", settings.db.password.get_secret_value(), settings.env
    )
    for user in users:
        validate_password(user, settings.env)
    engine = build_engine(settings.db)
    with engine.begin() as conn:
        for user in users:
            ensure_user(conn, user)
    engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
