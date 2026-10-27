from __future__ import annotations

import pytest
from pydantic import SecretStr

from datapulse.config import DatabaseSettings, Environment
from datapulse.db.bootstrap import ServiceUser, validate_password
from datapulse.db.engine import DatabaseUnavailableError, build_engine, check_connection


def test_unreachable_database_raises_without_leaking_password() -> None:
    # Puerto 1 en localhost: rechazo inmediato, no necesita un servidor real.
    db = DatabaseSettings(
        _env_file=None, host="127.0.0.1", port=1, password=SecretStr("leak-me"), connect_timeout_s=1
    )
    engine = build_engine(db)
    with pytest.raises(DatabaseUnavailableError) as info:
        check_connection(engine)
    assert "leak-me" not in str(info.value)
    assert "***" in str(info.value)


@pytest.mark.parametrize("env", list(Environment))
def test_empty_password_is_always_rejected(env: Environment) -> None:
    with pytest.raises(ValueError, match="vacía"):
        validate_password(ServiceUser("u", "g", SecretStr("")), env)


def test_short_password_rejected_only_in_prod() -> None:
    user = ServiceUser("u", "g", SecretStr("short"))
    validate_password(user, Environment.DEV)
    with pytest.raises(ValueError, match="prod"):
        validate_password(user, Environment.PROD)
