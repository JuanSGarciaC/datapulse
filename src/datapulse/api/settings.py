"""Configuración de la API (variables ``DATAPULSE_API_*``)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MIN_KEY_LENGTH = 16


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATAPULSE_API_", env_file=".env", extra="ignore")

    # Claves de lectura separadas por comas (permite rotarlas sin cortar el servicio).
    keys: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list)
    # Clave distinta y solo para disparar pipelines; sin ella el endpoint no existe (404).
    admin_key: SecretStr | None = None
    docs_enabled: bool = True
    max_page_size: Annotated[int, Field(ge=1, le=10_000)] = 500
    default_page_size: Annotated[int, Field(ge=1, le=10_000)] = 50

    @field_validator("keys", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        if isinstance(value, str):
            return [k.strip() for k in value.split(",") if k.strip()]
        return value

    @field_validator("keys")
    @classmethod
    def _strong_keys(cls, value: list[SecretStr]) -> list[SecretStr]:
        for key in value:
            if len(key.get_secret_value()) < MIN_KEY_LENGTH:
                raise ValueError(f"cada API key debe tener al menos {MIN_KEY_LENGTH} caracteres")
        return value

    @field_validator("admin_key")
    @classmethod
    def _strong_admin(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_KEY_LENGTH:
            raise ValueError(
                f"la clave de administración debe tener al menos {MIN_KEY_LENGTH} caracteres"
            )
        return value
