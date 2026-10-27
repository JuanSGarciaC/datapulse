from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from datapulse.config import DatabaseSettings, Environment, LogFormat, Settings, get_settings


def test_defaults_are_safe_for_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DATAPULSE_ENV", "DATAPULSE_LOG_LEVEL", "DATAPULSE_LOG_FORMAT"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.env is Environment.DEV
    assert settings.log_format is LogFormat.JSON


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAPULSE_ENV", "prod")
    monkeypatch.setenv("DATAPULSE_LOG_LEVEL", "debug")
    monkeypatch.setenv("DATAPULSE_DB_HOST", "db.internal")
    monkeypatch.setenv("DATAPULSE_DB_PORT", "6543")
    settings = get_settings()
    assert settings.env is Environment.PROD
    assert settings.log_level == "DEBUG"
    assert settings.db.host == "db.internal"
    assert settings.db.port == 6543


def test_url_escapes_special_characters_in_password() -> None:
    db = DatabaseSettings(_env_file=None, password=SecretStr("p@ss:w/rd#?"))
    url = db.url()
    assert url.password == "p@ss:w/rd#?"
    rendered = url.render_as_string(hide_password=False)
    assert "p%40ss%3Aw%2Frd%23%3F" in rendered


def test_password_never_appears_in_repr_or_rendered_url() -> None:
    db = DatabaseSettings(_env_file=None, password=SecretStr("super-secret-value"))
    assert "super-secret-value" not in repr(db)
    assert "super-secret-value" not in str(db.url())


@pytest.mark.parametrize(
    ("field", "value"),
    [("port", 0), ("port", 70000), ("sslmode", "yes-please"), ("pool_size", 0)],
)
def test_invalid_database_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        DatabaseSettings.model_validate({field: value})


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="LOUD")


def test_settings_are_immutable() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]
