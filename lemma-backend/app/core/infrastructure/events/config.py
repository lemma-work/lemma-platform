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

#: Dead-letter streams are named `{stream}:dead` at runtime, so they match no
#: static override and inherited the 50,000 default. Their entries carry up to
#: 64KB of quarantined body each, which is a 3.2GB ceiling per stream that
#: nothing consumes and nothing was watching.
_DEAD_LETTER_SUFFIX = ":dead"


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
    outbox_listen_enabled: bool = Field(
        default=True,
        description=(
            "Wake the outbox dispatcher with PostgreSQL LISTEN/NOTIFY instead "
            "of waiting out the idle backoff. The notification is only a hint: "
            "the fallback poll still runs and still delivers everything, so "
            "turning this off restores timer-driven behaviour exactly. "
            "Requires a direct connection -- a transaction-mode pooler in "
            "front of PostgreSQL silently swallows session-scoped LISTEN, "
            "which degrades to fallback latency rather than breaking. "
            "On by default because the backoff it replaces is the dominant "
            "term in how long a chat message waits before anything happens: "
            "an idle dispatcher sits at outbox_idle_poll_max_seconds, and a "
            "message landing mid-sleep waits out the remainder. Measured "
            "against a local stack, that was 1.4-4.1s per message."
        ),
    )
    outbox_listen_fallback_poll_seconds: float = Field(
        default=5.0,
        ge=0.5,
        description=(
            "Idle wait when a wake listener is attached. This is the worst-case "
            "delivery latency if every notification is lost -- a dropped "
            "listener, or a pooler that ate the LISTEN -- so it is a recovery "
            "bound, not a performance number. Ignored while "
            "outbox_listen_enabled is false. Deliberately no higher than "
            "outbox_idle_poll_max_seconds: attaching a listener must never "
            "make a deployment whose LISTEN is silently swallowed slower than "
            "the backoff ladder it replaced."
        ),
    )
    # Whole seconds: asyncpg's connect() types its timeout as an int.
    outbox_listen_connect_timeout_seconds: int = Field(default=10, gt=0)
    outbox_listen_health_interval_seconds: float = Field(
        default=30.0,
        gt=0,
        description=(
            "How often an idle listener proves its socket still works. A "
            "silently dead TCP connection does not fire asyncpg's termination "
            "callback, so without this the listener stays quiet forever and "
            "the dispatcher never learns it is only being served by the poll."
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
    redis_stream_max_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=0,
        description=(
            "Byte budget per stream, enforced by a periodic trim rather than on "
            "publish. The MAXLEN cap counts entries, and entries are not what "
            "runs out: the same 50,000 meant 9MB for one stream and 831MB for "
            "another. Only fully-consumed entries are removed, so this can "
            "never destroy unread work. 0 disables it. Env: "
            "``REDIS_STREAM_MAX_BYTES``."
        ),
    )
    redis_stream_pending_hold_seconds: int = Field(
        default=900,
        ge=0,
        description=(
            "How long one unacked message may hold a stream's size cap open. "
            "Past this the entry has already failed its consumer and the "
            "reclaimer, so it stops being a reason to retain everything behind "
            "it. Quarantine only fires after 12 deliveries, which a message "
            "nobody retries never reaches -- that is how a single entry with "
            "two deliveries disabled trimming on a production stream for hours. "
            "0 disables the escape hatch. Env: "
            "``REDIS_STREAM_PENDING_HOLD_SECONDS``."
        ),
    )
    redis_stream_dead_letter_maxlen: int = Field(
        default=1_000,
        ge=0,
        description=(
            "Cap for `{stream}:dead` quarantine streams, which carry up to 64KB "
            "of message body per entry and have no consumer. Env: "
            "``REDIS_STREAM_DEAD_LETTER_MAXLEN``."
        ),
    )
    redis_stream_hard_maxlen_multiplier: int = Field(
        default=4,
        ge=1,
        description=(
            "How far a stream may overshoot its MAXLEN while a consumer group "
            "is behind. Protecting unread work used to mean publishing with no "
            "cap at all, so one unacked message disabled trimming for the whole "
            "stream and it grew until Redis was OOM-killed. A stuck consumer "
            "must degrade to retaining more, never to retaining everything. "
            "Env: ``REDIS_STREAM_HARD_MAXLEN_MULTIPLIER``."
        ),
    )
    redis_stream_snapshot_interval_seconds: float = Field(default=300.0, ge=0)
    redis_stream_stale_consumer_seconds: int = Field(default=900, ge=1)
    consumer_group_reconcile_interval_seconds: float = Field(default=30.0, ge=0)
    event_completed_retention_days: int = Field(
        default=7,
        ge=1,
        description=(
            "How long a published outbox row or completed inbox row is kept. "
            "These are delivery receipts, not history: the event itself lives "
            "in its Redis stream and its effects live in the domain tables. "
            "Thirty days of them is what let the outbox reach several hundred "
            "thousand rows."
        ),
    )
    event_dead_letter_retention_days: int = Field(
        default=90,
        ge=1,
        description=(
            "How long a dead-lettered row is kept. Deliberately far longer "
            "than the completed window -- this set stays small and is the one "
            "an operator actually needs to read after an incident."
        ),
    )
    event_retention_batch_size: int = Field(default=1_000, ge=1, le=10_000)
    event_retention_run_budget_seconds: float = Field(
        default=45.0,
        ge=0.0,
        description=(
            "Wall-clock budget for one retention sweep. The sweep deletes in "
            "batches until a category is drained or this budget is spent, so a "
            "backlog larger than one batch is cleared over successive runs "
            "instead of never. Zero restores the old one-batch-per-category "
            "behaviour. Keep it well under the cron period."
        ),
    )

    def stream_maxlen_for(self, stream: str) -> int | None:
        default = self.redis_stream_maxlen
        if stream.endswith(_DEAD_LETTER_SUFFIX):
            default = min(default, self.redis_stream_dead_letter_maxlen)
        configured = self.redis_stream_maxlen_overrides.get(stream, default)
        return configured if configured > 0 else None

    def stream_hard_maxlen_for(self, stream: str) -> int | None:
        """The ceiling a stream may never pass, however far behind a group is.

        ``None`` only when trimming is switched off outright, which is the one
        case where "no cap" is a deliberate choice rather than an accident.
        """
        configured = self.stream_maxlen_for(stream)
        if configured is None:
            return None
        return configured * self.redis_stream_hard_maxlen_multiplier


event_transport_settings = EventTransportSettings()
