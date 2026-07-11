"""Configuration owned by the durable event transport."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class EventTransportSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    event_publish_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Total timeout for consumer-group validation and Redis XADD.",
    )
    redis_stream_polling_interval_ms: int = Field(default=500, gt=0)
    redis_stream_min_idle_time_ms: int = Field(default=60_000, gt=0)
    consumer_group_reconcile_interval_seconds: float = Field(default=30.0, ge=0)
    event_completed_retention_days: int = Field(default=30, ge=1)
    event_dead_letter_retention_days: int = Field(default=90, ge=1)
    event_retention_batch_size: int = Field(default=1_000, ge=1, le=10_000)


event_transport_settings = EventTransportSettings()
