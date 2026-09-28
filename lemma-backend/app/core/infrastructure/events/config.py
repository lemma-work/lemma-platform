"""Configuration owned by the durable event transport."""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


_DEFAULT_STREAM_MAXLEN_OVERRIDES = {
    "usage_events": 10_000,
}

#: Streams nothing publishes to or reads any more, deleted by the stream guard.
#: ``webhook_events`` held raw inbound webhooks -- 65MB in production long after
#: its last publisher was removed, because nothing trims a stream nothing
#: writes to: trimming happens on write.
RETIRED_STREAMS: tuple[str, ...] = ("webhook_events",)

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
    redis_streams_memory_fraction: float = Field(
        default=0.5,
        gt=0,
        le=0.9,
        description=(
            "Share of Redis ``maxmemory`` that event streams may occupy between "
            "them. One budget for all of them, not one per stream: per-stream "
            "budgets summed to several times the memory Redis actually had. "
            "Enforced every guard pass by trimming the largest streams first -- "
            "fully-consumed entries before anything else, and unread entries "
            "only when that is not enough, recording what was trimmed so it is "
            "replayed from the outbox. Env: ``REDIS_STREAMS_MEMORY_FRACTION``."
        ),
    )
    redis_streams_budget_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=0,
        description=(
            "Stream budget when Redis reports no ``maxmemory`` (0, unlimited), "
            "which is what a bare ``redis-server`` does. 0 disables the budget "
            "in that case. Env: ``REDIS_STREAMS_BUDGET_BYTES``."
        ),
    )
    redis_memory_warn_ratio: float = Field(
        default=0.70,
        gt=0,
        lt=1,
        description=(
            "Redis memory use, as a share of ``maxmemory``, above which the "
            "stream budget shrinks to whatever keeps Redis under this line "
            "given everything else it holds. Env: ``REDIS_MEMORY_WARN_RATIO``."
        ),
    )
    redis_memory_critical_ratio: float = Field(
        default=0.85,
        gt=0,
        lt=1,
        description=(
            "Redis memory use above which the outbox dispatcher stops "
            "publishing. Events wait in PostgreSQL -- durably, and at no cost "
            "to Redis -- until use falls below "
            "``REDIS_MEMORY_RESUME_RATIO``. Env: ``REDIS_MEMORY_CRITICAL_RATIO``."
        ),
    )
    redis_memory_resume_ratio: float = Field(
        default=0.60,
        gt=0,
        lt=1,
        description=(
            "Redis memory use below which a paused outbox dispatcher resumes. "
            "Below the critical ratio so the dispatcher does not flap. Env: "
            "``REDIS_MEMORY_RESUME_RATIO``."
        ),
    )
    redis_stream_guard_interval_seconds: float = Field(
        default=30.0,
        ge=0,
        description=(
            "How often the worker enforces the stream budget, checks Redis "
            "memory, looks for stalled consumer groups and replays trimmed "
            "gaps. It was an hourly cron; an hour of a bulk import is more than "
            "a small Redis holds. 0 disables the guard. Env: "
            "``REDIS_STREAM_GUARD_INTERVAL_SECONDS``."
        ),
    )
    redis_stream_stall_seconds: int = Field(
        default=900,
        ge=0,
        description=(
            "How long a consumer group this worker reads may sit behind with "
            "its reader not reading before the worker restarts itself. Must "
            "exceed the handler timeout, which is the longest a live reader "
            "can legitimately go without reading. 0 disables it. Env: "
            "``REDIS_STREAM_STALL_SECONDS``."
        ),
    )
    redis_stream_gap_replay_batch_size: int = Field(
        default=1_000,
        ge=1,
        le=50_000,
        description=(
            "Outbox rows re-published per guard pass while replaying entries a "
            "stream lost to the memory budget. A batch is only started once the "
            "group has read the previous one, so replay runs at the pace of the "
            "slowest reader instead of refilling the stream it was trimmed "
            "from. Env: ``REDIS_STREAM_GAP_REPLAY_BATCH_SIZE``."
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
    redis_stream_handler_timeout_seconds: float = Field(
        default=300.0,
        ge=0,
        description=(
            "Deadline for one stream delivery. A subscriber handles one "
            "message at a time, so a handler that never returns stops its "
            "whole lane while its reader task still looks alive. Past this the "
            "delivery fails with TimeoutError and takes the ordinary retry and "
            "quarantine path. Handlers enqueue work rather than do it, so "
            "minutes is generous. 0 disables it. Env: "
            "``REDIS_STREAM_HANDLER_TIMEOUT_SECONDS``."
        ),
    )
    redis_stream_snapshot_interval_seconds: float = Field(default=300.0, ge=0)
    redis_stream_stale_consumer_seconds: int = Field(default=900, ge=1)
    consumer_group_reconcile_interval_seconds: float = Field(default=30.0, ge=0)
    redis_stream_group_reap_after_seconds: int = Field(
        default=86_400,
        ge=0,
        description=(
            "How long a consumer group must go unclaimed by every process in "
            "the fleet -- with nothing pending, nothing delivered, and no "
            "consumer that is not idle -- before it counts as abandoned. Must "
            "be far larger than "
            "``consumer_group_reconcile_interval_seconds``, which is how often "
            "a live process renews its claim, and larger than any rollout or "
            "planned outage: a group destroyed while its deployment is merely "
            "down loses its pending-entries list. 0 disables the reaper. Env: "
            "``REDIS_STREAM_GROUP_REAP_AFTER_SECONDS``."
        ),
    )
    redis_stream_group_destroy_enabled: bool = Field(
        default=False,
        description=(
            "Whether an abandoned group is destroyed or only reported. Off by "
            "default, which is the point: the warning names every candidate, so "
            "the set can be read against what is expected before anything is "
            "deleted. XGROUP DESTROY removes the group's pending-entries list "
            "with it, and a wrong answer here is unrecoverable, so this earns "
            "its observation period rather than assuming one. Env: "
            "``REDIS_STREAM_GROUP_DESTROY_ENABLED``."
        ),
    )
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
        default=14,
        ge=1,
        description=(
            "How long a dead-lettered row is kept. Longer than the completed "
            "window -- this set stays small and is the one an operator "
            "actually needs to read after an incident -- but no longer than "
            "two weeks: at 90 days production was still holding dead letters "
            "from two months earlier, which nobody had read and nobody was "
            "going to. Env: ``EVENT_DEAD_LETTER_RETENTION_DAYS``, so an "
            "install that wants a longer forensic window can say so."
        ),
    )
    event_abandoned_retention_days: int = Field(
        default=14,
        ge=1,
        description=(
            "How long a row stuck in PROCESSING or RETRYING is kept. These "
            "match neither of the other two windows -- one keys off "
            "``completed_at`` and the other off ``dead_lettered_at``, and an "
            "abandoned row has neither -- so nothing ever deleted them and "
            "they accumulated for as long as the table had existed. A claim "
            "that has not moved in two weeks is not in flight; it is debris "
            "from a process that died."
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

    @model_validator(mode="after")
    def _memory_ratios_are_ordered(self) -> "EventTransportSettings":
        """Resume below critical, or a paused dispatcher flaps on every pass."""
        if not (
            self.redis_memory_resume_ratio < self.redis_memory_critical_ratio
            and self.redis_memory_warn_ratio <= self.redis_memory_critical_ratio
        ):
            raise ValueError(
                "REDIS_MEMORY_RESUME_RATIO must be below REDIS_MEMORY_CRITICAL_RATIO, "
                "and REDIS_MEMORY_WARN_RATIO must not exceed it"
            )
        return self

    @model_validator(mode="after")
    def _reap_window_outlives_a_claim(self) -> "EventTransportSettings":
        """A reap window shorter than the renewal interval reaps live groups.

        The reaper's only evidence that a group is still wanted is a claim
        renewed every ``consumer_group_reconcile_interval_seconds``. If the
        window is shorter than that, every claim looks stale in the gap between
        one renewal and the next -- so a perfectly healthy group with an empty
        backlog becomes a candidate, and with destruction enabled it loses its
        pending-entries list. The default is 24h against 30s, but these are two
        independent environment variables and nothing otherwise relates them.

        Zero still disables the reaper; that is not a short window, it is none.
        """
        window = self.redis_stream_group_reap_after_seconds
        interval = self.consumer_group_reconcile_interval_seconds
        if window and window < interval:
            raise ValueError(
                "redis_stream_group_reap_after_seconds must be 0 (disabled) or "
                f"at least consumer_group_reconcile_interval_seconds ({interval}); "
                f"got {window}. A shorter window reaps groups that are alive."
            )
        return self

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
