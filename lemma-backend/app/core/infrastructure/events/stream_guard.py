"""The worker's standing guarantee that event streams cannot run Redis out of memory.

Runs every ``REDIS_STREAM_GUARD_INTERVAL_SECONDS`` in every worker. One pass:

1. **Reads Redis memory** (``INFO memory``) and works out how much of it the
   streams may have: ``REDIS_STREAMS_MEMORY_FRACTION`` of ``maxmemory``, shrunk
   further if everything else in Redis has pushed total use past
   ``REDIS_MEMORY_WARN_RATIO``.
2. **Raises or clears the memory-pressure flag.** Above
   ``REDIS_MEMORY_CRITICAL_RATIO`` the outbox dispatcher stops publishing and
   events wait in PostgreSQL; below ``REDIS_MEMORY_RESUME_RATIO`` it resumes.
3. **Enforces the stream budget** (``stream_budget``), recording a gap for any
   group that loses unread entries.
4. **Publishes a per-stream entry cap** that every XADD applies, so a burst
   between two passes cannot outrun the budget.
5. **Looks for a stalled group** this worker reads -- behind, with its reader
   not reading -- and hands it to the lane watchdog, which restarts the process.
6. **Replays recorded gaps** whose groups are reading again (``gap_replay``).
7. **Updates the stream gauges.**

Every step is independent: a failure in one is reported and the rest still run,
because the step most likely to fail -- anything that writes -- is also the one
Redis refuses when it is fullest, which is exactly when trimming has to work.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.events.config import (
    RETIRED_STREAMS,
    event_transport_settings,
)
from app.core.infrastructure.events.gap_replay import record_gap, replay_gaps
from app.core.infrastructure.events.stream_budget import (
    StreamState,
    enforce_stream_budget,
    publish_entry_cap,
    int_field,
    read_stream_state,
)
from app.core.infrastructure.events.stream_keys import (
    MEMORY_PRESSURE_KEY,
    stream_cap_key,
)
from app.core.infrastructure.events.stream_observability import observable_streams
from app.core.infrastructure.events.stream_subscriber import subscribed_stream_groups
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)

_MEMORY_PRESSURE_TTL_SECONDS = 120
_STREAM_CAP_TTL_SECONDS = 300
#: How often a sustained condition is repeated in the log.
_REPORT_INTERVAL_SECONDS = 300.0


def _is_job_queue(stream: str) -> bool:
    return stream.startswith("streaq:") and ":queues:" in stream


@dataclass(slots=True)
class RedisMemory:
    used: int
    maximum: int

    @property
    def ratio(self) -> float | None:
        return self.used / self.maximum if self.maximum > 0 else None


def streams_budget(memory: RedisMemory | None, streams_bytes: int) -> int:
    """How many bytes all streams together may hold right now.

    With a ``maxmemory`` it is the configured share of it -- and never more than
    what keeps *total* use under the warn ratio, given everything else Redis is
    holding. Without one, the configured fallback.
    """
    settings = event_transport_settings
    if memory is None or memory.maximum <= 0:
        return settings.redis_streams_budget_bytes
    share = int(memory.maximum * settings.redis_streams_memory_fraction)
    others = max(0, memory.used - streams_bytes)
    headroom = int(memory.maximum * settings.redis_memory_warn_ratio) - others
    return max(0, min(share, headroom))


@dataclass
class _Snapshot:
    """Last readings, held for the synchronous OpenTelemetry callbacks."""

    memory: RedisMemory | None = None
    streams: dict[str, StreamState] = field(default_factory=dict)
    budget: int | None = None


_snapshot = _Snapshot()


def _observe_memory(options: CallbackOptions) -> Iterable[Observation]:
    del options
    memory = _snapshot.memory
    if memory is None:
        return []
    observations = [Observation(memory.used, {"kind": "used"})]
    if memory.maximum > 0:
        observations.append(Observation(memory.maximum, {"kind": "max"}))
    if _snapshot.budget is not None:
        observations.append(Observation(_snapshot.budget, {"kind": "streams_budget"}))
    return observations


def _observe_stream_memory(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(state.memory, {"stream": name})
        for name, state in _snapshot.streams.items()
    ]


def _observe_stream_length(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(state.length, {"stream": name})
        for name, state in _snapshot.streams.items()
    ]


def _observe_group_lag(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        # An unknown lag (entries past the group were trimmed) is reported as
        # the whole stream: it is at least that far behind, and a gauge that
        # drops out exactly when a group is furthest behind is worse than none.
        Observation(
            group.lag if group.lag is not None else state.length,
            {"stream": name, "group": group.name},
        )
        for name, state in _snapshot.streams.items()
        for group in state.groups
    ]


def _observe_group_pending(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(group.pending, {"stream": name, "group": group.name})
        for name, state in _snapshot.streams.items()
        for group in state.groups
    ]


def _observe_reader_inactive(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(
            group.reader_inactive_ms / 1000, {"stream": name, "group": group.name}
        )
        for name, state in _snapshot.streams.items()
        for group in state.groups
        if group.reader_inactive_ms is not None
    ]


meter.create_observable_gauge(
    "lemma.redis.memory.bytes",
    callbacks=[_observe_memory],
    unit="By",
    description="Redis memory in use, its maxmemory, and the share streams may hold.",
)
meter.create_observable_gauge(
    "lemma.redis.stream.memory",
    callbacks=[_observe_stream_memory],
    unit="By",
    description="Bytes held by each event stream and job queue.",
)
meter.create_observable_gauge(
    "lemma.redis.stream.length",
    callbacks=[_observe_stream_length],
    description="Entries in each event stream and job queue.",
)
meter.create_observable_gauge(
    "lemma.redis.stream.group.lag",
    callbacks=[_observe_group_lag],
    description="Entries a consumer group has not yet read.",
)
meter.create_observable_gauge(
    "lemma.redis.stream.group.pending",
    callbacks=[_observe_group_pending],
    description="Entries delivered to a consumer group and not yet acknowledged.",
)
meter.create_observable_gauge(
    "lemma.redis.stream.group.reader_inactive",
    callbacks=[_observe_reader_inactive],
    unit="s",
    description="Seconds since a consumer group's reader last read an entry.",
)


class _Throttle:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}

    def due(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last.get(key)
        if last is not None and now - last < _REPORT_INTERVAL_SECONDS:
            return False
        self._last[key] = now
        return True


_throttle = _Throttle()
#: What a guard step may fail with and still leave the guard running: Redis or
#: the database being unavailable, which is exactly when the next pass matters.
_REDIS_FAILURES = (RedisError, OSError)
_STEP_FAILURES = (RedisError, OSError, SQLAlchemyError)
_step_incidents: dict[
    str, DependencyIncident
] = {}  # memory: bounded one per guard step and declared stream


def _incident(step: str) -> DependencyIncident:
    return _step_incidents.setdefault(
        step, DependencyIncident(f"redis.stream.guard:{step}", logger=logger)
    )


async def read_redis_memory(client) -> RedisMemory:
    info = await client.info("memory")
    return RedisMemory(
        used=int_field(info, "used_memory"), maximum=int_field(info, "maxmemory")
    )


async def update_memory_pressure(client, memory: RedisMemory) -> bool | None:
    """Raise or clear the flag that pauses publishing; returns the new state.

    ``None`` when Redis has no ``maxmemory`` -- no ratio, no flag.
    """
    ratio = memory.ratio
    if ratio is None:
        return None
    settings = event_transport_settings
    if ratio >= settings.redis_memory_critical_ratio:
        if _throttle.due("critical"):
            logger.error(
                "redis.memory.critical",
                used_bytes=memory.used,
                max_bytes=memory.maximum,
                ratio=round(ratio, 3),
            )
        await client.set(
            MEMORY_PRESSURE_KEY, str(round(ratio, 3)), ex=_MEMORY_PRESSURE_TTL_SECONDS
        )
        return True
    if ratio >= settings.redis_memory_warn_ratio and _throttle.due("warn"):
        logger.warning(
            "redis.memory.pressure.degraded",
            used_bytes=memory.used,
            max_bytes=memory.maximum,
            ratio=round(ratio, 3),
        )
    if ratio < settings.redis_memory_resume_ratio:
        await client.delete(MEMORY_PRESSURE_KEY)
        return False
    # Between resume and critical: leave whatever state the flag is in, so a
    # paused dispatcher does not resume the moment it dips below critical.
    return bool(await client.exists(MEMORY_PRESSURE_KEY))


async def read_stream_states(client, streams: Iterable[str]) -> list[StreamState]:
    states: list[StreamState] = []
    for stream in sorted(streams):
        try:
            state = await read_stream_state(
                client, stream, trimmable=not _is_job_queue(stream)
            )
        except _REDIS_FAILURES as exc:
            _incident(f"read:{stream}").record_failure(error_type=type(exc).__name__)
            continue
        _incident(f"read:{stream}").record_success()
        if state is not None:
            states.append(state)
    return states


def find_stalled_groups(
    states: Iterable[StreamState],
    consumed_here: set[tuple[str, str]],
    *,
    uptime_seconds: float,
) -> list[tuple[str, str]]:
    """Groups this process reads that are behind while their reader is idle.

    Only groups this process consumes: another deployment's group is not this
    process's to restart over. Not before the process has been up for the
    stall window, because a group's reader timings are from whichever process
    read it last.
    """
    stall_seconds = event_transport_settings.redis_stream_stall_seconds
    if stall_seconds <= 0 or uptime_seconds < stall_seconds:
        return []
    stall_ms = stall_seconds * 1000
    stalled: list[tuple[str, str]] = []
    for state in states:
        for group in state.groups:
            if (state.name, group.name) not in consumed_here:
                continue
            if not state.group_is_behind(group):
                continue
            if group.reader_inactive_ms is None or group.reader_inactive_ms > stall_ms:
                stalled.append((state.name, group.name))
    return stalled


async def publish_stream_caps(
    client, states: Iterable[StreamState], budget: int
) -> None:
    for state in states:
        if not state.trimmable:
            continue
        cap = publish_entry_cap(state, budget)
        if cap is None:
            continue
        await client.set(stream_cap_key(state.name), cap, ex=_STREAM_CAP_TTL_SECONDS)


async def _step(name: str, action: Awaitable[object]) -> None:
    """Run one guard step; a Redis or database failure is reported, not raised.

    Narrow on purpose. Anything else is a bug in the guard, and a guard that
    has stopped guarding must not look like one that is working: it escapes,
    ends the guard task, and the lane watchdog restarts the worker.
    """
    try:
        await action
    except _STEP_FAILURES as exc:
        _incident(name).record_failure(error_type=type(exc).__name__)
    else:
        _incident(name).record_success()


async def _enforce_budget(client, states: list[StreamState], budget: int) -> None:
    outcome = await enforce_stream_budget(client, states, budget)
    if outcome.reclaimed:
        logger.info(
            "redis.stream.budget_trimmed.observed",
            streams=len(outcome.reclaimed),
            reclaimed_bytes=sum(outcome.reclaimed.values()),
        )
    for gap in outcome.gaps:
        await record_gap(client, gap)
    if outcome.still_over and _throttle.due("still_over"):
        logger.error(
            "redis.stream.over_budget.failed",
            memory_bytes=outcome.total_bytes,
            budget_bytes=budget,
        )


def _report_stalled_groups(
    states: list[StreamState],
    on_stalled_group: Callable[[str, str], None] | None,
    uptime_seconds: float,
) -> None:
    for stream, group in find_stalled_groups(
        states, subscribed_stream_groups(), uptime_seconds=uptime_seconds
    ):
        logger.error(
            "redis.stream.group_stalled",
            stream_name=stream,
            group=group,
            stall_seconds=event_transport_settings.redis_stream_stall_seconds,
        )
        if on_stalled_group is not None:
            on_stalled_group(stream, group)


async def _read_memory(client) -> RedisMemory | None:
    try:
        memory = await read_redis_memory(client)
    except _REDIS_FAILURES as exc:
        _incident("memory").record_failure(error_type=type(exc).__name__)
        return None
    _incident("memory").record_success()
    return memory


async def run_guard_pass(
    client,
    *,
    session_maker: Callable[[], AsyncSession] | None,
    on_stalled_group: Callable[[str, str], None] | None,
    uptime_seconds: float,
) -> None:
    """One pass of the guard; see the module docstring for the steps."""
    memory = await _read_memory(client)
    states = await read_stream_states(client, observable_streams())
    budget = streams_budget(memory, sum(state.memory for state in states))
    if memory is not None:
        await _step("pressure", update_memory_pressure(client, memory))
    await _step("budget", _enforce_budget(client, states, budget))
    await _step("caps", publish_stream_caps(client, states, budget))
    _report_stalled_groups(states, on_stalled_group, uptime_seconds)
    if session_maker is not None:
        groups = {
            (state.name, group.name): group
            for state in states
            for group in state.groups
        }
        await _step("replay", replay_gaps(client, session_maker, groups))
    _snapshot.memory = memory
    _snapshot.streams = {state.name: state for state in states}
    _snapshot.budget = budget


async def retire_streams(client) -> None:
    """Delete streams nothing uses any more; see ``RETIRED_STREAMS``."""
    try:
        deleted = int(await client.delete(*RETIRED_STREAMS) or 0)
    except _REDIS_FAILURES as exc:
        _incident("retire").record_failure(error_type=type(exc).__name__)
        return
    if deleted:
        logger.info("redis.stream.retired", streams=",".join(RETIRED_STREAMS))


async def stream_guard_loop(
    message_bus,
    *,
    session_maker: Callable[[], AsyncSession] | None = None,
    on_stalled_group: Callable[[str, str], None] | None = None,
) -> None:
    """Run :func:`run_guard_pass` at the configured cadence, forever."""
    interval = event_transport_settings.redis_stream_guard_interval_seconds
    if interval <= 0:
        return
    started = time.monotonic()
    client = await message_bus.redis_client()
    await retire_streams(client)
    while True:
        await asyncio.sleep(interval)
        await run_guard_pass(
            client,
            session_maker=session_maker,
            on_stalled_group=on_stalled_group,
            uptime_seconds=time.monotonic() - started,
        )
