import pytest
from pydantic import ValidationError

from ledger.config import Settings

VALID_ENV = {
    "LEDGER_DATABASE_URL": "postgresql+asyncpg://ledger:ledger@db:5432/ledger",
    "LEDGER_REDIS_URL": "redis://cache:6379/0",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def load_settings() -> Settings:
    # A developer's local .env must not leak into the test run.
    return Settings(_env_file=None)


def test_reads_prefixed_environment_variables(env: pytest.MonkeyPatch) -> None:
    env.setenv("LEDGER_LOG_LEVEL", "DEBUG")

    settings = load_settings()

    assert str(settings.database_url) == VALID_ENV["LEDGER_DATABASE_URL"]
    assert str(settings.redis_url) == VALID_ENV["LEDGER_REDIS_URL"]
    assert settings.log_level == "DEBUG"


@pytest.mark.usefixtures("env")
def test_log_level_defaults_to_info() -> None:
    assert load_settings().log_level == "INFO"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LEDGER_DATABASE_URL", "mysql://ledger:ledger@db:3306/ledger"),
        ("LEDGER_REDIS_URL", "http://cache:6379"),
        ("LEDGER_LOG_LEVEL", "VERBOSE"),
    ],
)
def test_rejects_invalid_values(env: pytest.MonkeyPatch, name: str, value: str) -> None:
    env.setenv(name, value)

    with pytest.raises(ValidationError, match=name.removeprefix("LEDGER_").lower()):
        load_settings()


def test_database_url_is_required(env: pytest.MonkeyPatch) -> None:
    env.delenv("LEDGER_DATABASE_URL")

    with pytest.raises(ValidationError, match="database_url"):
        load_settings()
