"""Configuración centralizada a partir de variables de entorno.

Todas las variables usan el prefijo ``DATAPULSE_``. Los secretos se tipan como
``SecretStr`` para que nunca aparezcan en ``repr()``, logs ni trazas de error.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


Port = Annotated[int, Field(ge=1, le=65535)]


class DatabaseSettings(BaseSettings):
    """Conexión a PostgreSQL. Cada componente usa su propio usuario (mínimo privilegio)."""

    model_config = SettingsConfigDict(
        env_prefix="DATAPULSE_DB_", env_file=".env", extra="ignore", frozen=True
    )

    host: str = "localhost"
    port: Port = 5432
    name: str = "datapulse"
    user: str = "datapulse_admin"
    password: SecretStr = SecretStr("")
    pool_size: Annotated[int, Field(ge=1, le=50)] = 5
    connect_timeout_s: Annotated[int, Field(ge=1, le=60)] = 5
    sslmode: str = "prefer"

    @field_validator("sslmode")
    @classmethod
    def _valid_sslmode(cls, value: str) -> str:
        allowed = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
        if value not in allowed:
            raise ValueError(f"sslmode debe ser uno de {sorted(allowed)}")
        return value

    def url(self) -> URL:
        """URL de SQLAlchemy construida con ``URL.create`` para escapar caracteres especiales."""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value() or None,
            host=self.host,
            port=self.port,
            database=self.name,
            query={"sslmode": self.sslmode, "connect_timeout": str(self.connect_timeout_s)},
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATAPULSE_", env_file=".env", extra="ignore", frozen=True
    )

    env: Environment = Environment.DEV
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        upper = value.upper()
        if upper not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level inválido")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instancia única y cacheada. En pruebas se limpia con ``get_settings.cache_clear()``."""
    return Settings()
