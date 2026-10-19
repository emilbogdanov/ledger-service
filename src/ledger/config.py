from typing import Literal

from pydantic import Field, PositiveFloat, PositiveInt, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEDGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: PostgresDsn
    redis_url: RedisDsn
    events_stream: str = Field(default="ledger:events", min_length=1)
    events_stream_max_length: PositiveInt = 1_000_000
    outbox_batch_size: PositiveInt = 100
    outbox_poll_interval_seconds: PositiveFloat = 1.0
    outbox_max_backoff_seconds: PositiveFloat = 30.0
