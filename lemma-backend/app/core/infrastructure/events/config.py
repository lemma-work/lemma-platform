"""Configuration owned by the durable event transport."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


_DEFAULT_STREAM_MAXLEN_OVERRIDES = {
    # Webhook payloads are much larger than ordinary domain events. Keeping
    # 50k of them can consume hundreds of MiB even in a low-traffic install.
    "webhook_events": 1_000,
    "usage_events": 10_000,
}


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
    outbox_idle_poll_max_seconds: float = Field(
        default=5.0,
        ge=0.5,
        description=(
            "Maximum adaptive idle delay for the PostgreSQL outbox dispatcher. "
            "The delay resets after any claimed batch."
        ),
    )
    redis_stream_polling_interval_ms: int = Field(default=500, gt=0)
    redis_stream_min_idle_time_ms: int = Field(default=60_000, gt=0)
    redis_stream_maxlen: int = Field(
        default=50_000,
        ge=0,
        description=(
            "Default approximate Redis Stream cap. Zero disables trimming. "
            "Grouped streams are capped only when live group state proves the "
            "retained window cannot remove pending or unread entries."
        ),
    )
    redis_stream_maxlen_overrides: dict[str, int] = Field(
        default_factory=lambda: dict(_DEFAULT_STREAM_MAXLEN_OVERRIDES),
        description="Per-stream MAXLEN overrides encoded as a JSON object.",
    )
    redis_stream_snapshot_interval_seconds: float = Field(default=300.0, ge=0)
    redis_stream_stale_consumer_seconds: int = Field(default=900, ge=1)
    consumer_group_reconcile_interval_seconds: float = Field(default=30.0, ge=0)
    event_completed_retention_days: int = Field(default=30, ge=1)
    event_dead_letter_retention_days: int = Field(default=90, ge=1)
    event_retention_batch_size: int = Field(default=1_000, ge=1, le=10_000)

    def stream_maxlen_for(self, stream: str) -> int | None:
        configured = self.redis_stream_maxlen_overrides.get(
            stream, self.redis_stream_maxlen
        )
        return configured if configured > 0 else None


event_transport_settings = EventTransportSettings()
