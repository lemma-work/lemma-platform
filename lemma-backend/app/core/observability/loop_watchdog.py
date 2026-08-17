"""Event-loop lag watchdog + liveness heartbeat.

The worker and API run everything on one event loop; if something blocks it, the
process stops making progress but the OS process stays alive, so nothing
restarts it. This watchdog makes a wedged loop *observable* and *actionable*:

- It schedules a wake-up every ``interval`` and measures how late it actually
  fires. That delay is the event-loop lag; a healthy loop is ~0, a blocked loop
  climbs. The value is exposed via :func:`get_loop_lag_seconds`.
- It writes a heartbeat file (current epoch seconds) each tick. A wedged loop
  can't update it, so an external liveness probe can check the file's freshness
  and restart the process. This is how the worker (which has no HTTP server)
  gets a liveness signal; the API additionally serves ``/health/live``.

Loop-lag telemetry is **stateful**. While degraded, the in-memory maximum lag is
tracked without emitting a warning on every tick; a single
``runtime.loop_lag.degraded`` event fires after three warning samples or one
five-second unhealthy sample. Recovery emits one
``runtime.loop_lag.recovered`` event after 30 healthy seconds, so threshold
jitter does not alternate events.

Mirrors the background-task shape of ``_consumer_group_reconcile_loop`` in the
streaq runtime: started in the lifespan, cancelled on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import time

from app.core.concurrency.offload import run_blocking
from app.core.config import settings
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.core.observability.stall_sampler import (
    keep_loop_tick_fresh,
    start_loop_stall_sampler,
    stop_loop_stall_sampler,
)

logger = get_logger(__name__)

@dataclass
class _LagGauge:
    """Most-recent measured event-loop lag, in seconds.

    An object rather than a bare module global because the watchdog coroutine
    is the only writer and ``/health/live`` and the metrics reader are the only
    readers — none of which hold a reference to the task. A global would need a
    ``global`` declaration in a coroutine that writes it and never reads it,
    which reads as a dead store to anyone (and to any analyser) looking at that
    function alone.
    """

    seconds: float = 0.0


_lag = _LagGauge()

# Degraded-state machine for loop-lag telemetry. Module-global so the watchdog
# task and tests can reset/inspect it without holding a reference to the task.
_degraded: bool = False
_degraded_since: float = 0.0  # time.monotonic() at degraded transition
_max_lag_seconds: float = 0.0  # peak lag observed during the current degraded window
_warning_streak: int = 0
_healthy_since: float | None = None
_breach_count: int = 0
_last_incident_at: float = -1e9

_last_unhealthy_at: float | None = None

_WARN_SAMPLES_TO_DEGRADE = 3
_RECOVERY_HEALTHY_SECONDS = 30.0
# How long a finished stall keeps `/health/live` failing. Long enough that a
# prober on a multi-second period observes it, short enough to sit well inside
# any realistic `periodSeconds x failureThreshold` window so a recovered
# process is never restarted for a stall it survived.
_LIVENESS_STICKY_SECONDS = 5.0
_INCIDENT_COOLDOWN_SECONDS = 300.0


def get_loop_lag_seconds() -> float:
    return _lag.seconds


def is_loop_healthy() -> bool:
    """False while the loop is stalling, plus a short tail (for ``/health/live``).

    The last sample alone is a poor probe answer: a process that spent four
    seconds wedged reports the stall on exactly one probe and reads healthy on
    the next, so a liveness check on a multi-second interval sees a wedged
    process as fine nearly every time. Hence the tail -- a stall stays visible
    for ``_LIVENESS_STICKY_SECONDS`` after it ends, long enough for a prober to
    catch it.

    It is deliberately NOT the ``_degraded`` flag, which holds for
    ``_RECOVERY_HEALTHY_SECONDS`` (30) so the telemetry does not flap. Liveness
    is not telemetry: with a typical ``periodSeconds: 10`` and
    ``failureThreshold: 3``, a 30-second unhealthy window is exactly a kill, so
    tying liveness to it converts every recovered stall into a guaranteed
    restart.

    A loop that is genuinely wedged keeps failing the current-lag check on its
    own merit and still gets restarted -- stickiness only ever changes the
    answer for a process that has already recovered, which is the one case
    where restarting is pure harm.
    """
    if _lag.seconds >= settings.loop_lag_unhealthy_seconds:
        return False
    if _last_unhealthy_at is None:
        return True
    return (
        time.monotonic() - _last_unhealthy_at
    ) >= _LIVENESS_STICKY_SECONDS


def reset_loop_watchdog_state() -> None:
    """Reset the degraded-state machine (for tests and process restart)."""
    global _degraded, _degraded_since, _max_lag_seconds, _warning_streak
    global _healthy_since, _breach_count, _last_incident_at, _last_unhealthy_at
    _degraded = False
    _last_unhealthy_at = None
    _degraded_since = 0.0
    _max_lag_seconds = 0.0
    _warning_streak = 0
    _healthy_since = None
    _breach_count = 0
    _last_incident_at = -1e9
    _lag.seconds = 0.0


def _evaluate_lag(
    lag: float,
    warn: float,
    *,
    service_name: str,
    now: float | None = None,
) -> None:
    """Stateful per-sample loop-lag telemetry.

    Emits ``runtime.loop_lag.degraded`` once on the transition into degraded,
    tracks the peak lag silently while degraded (no per-tick warning), and emits
    ``runtime.loop_lag.recovered`` once after 30 healthy seconds. ``now`` defaults
    to ``time.monotonic()`` and is overridable for tests so degraded-duration is
    deterministic.
    """
    global _degraded, _degraded_since, _max_lag_seconds, _warning_streak
    global _healthy_since, _breach_count, _last_incident_at, _last_unhealthy_at
    clock = time.monotonic() if now is None else now
    unhealthy = settings.loop_lag_unhealthy_seconds
    if lag >= unhealthy:
        # Stamped on every unhealthy sample, so `/health/live` keeps failing for
        # a few seconds after the stall ends and a prober on a multi-second
        # period can still see it.
        _last_unhealthy_at = clock
    if lag > warn:
        _healthy_since = None
        _warning_streak += 1
        is_unhealthy = lag >= unhealthy
        should_enter = is_unhealthy or _warning_streak >= _WARN_SAMPLES_TO_DEGRADE
        cooldown_elapsed = clock - _last_incident_at >= _INCIDENT_COOLDOWN_SECONDS
        if not _degraded and should_enter and cooldown_elapsed:
            _degraded = True
            _degraded_since = clock
            _max_lag_seconds = lag
            _breach_count = _warning_streak
            _last_incident_at = clock
            logger.warning(
                "runtime.loop_lag.degraded",
                lag_ms=round(lag * 1000, 1),
                threshold_ms=round(warn * 1000, 1),
                service=service_name,
                breach_count=_breach_count,
                unhealthy=is_unhealthy,
            )
        elif _degraded:
            _breach_count += 1
            if lag > _max_lag_seconds:
                _max_lag_seconds = lag
    elif _degraded:
        _warning_streak = 0
        if _healthy_since is None:
            _healthy_since = clock
        if clock - _healthy_since >= _RECOVERY_HEALTHY_SECONDS:
            degraded_duration_ms = round((clock - _degraded_since) * 1000, 1)
            logger.info(
                "runtime.loop_lag.recovered",
                max_lag_ms=round(_max_lag_seconds * 1000, 1),
                degraded_duration_ms=degraded_duration_ms,
                service=service_name,
                breach_count=_breach_count,
            )
            _degraded = False
            _degraded_since = 0.0
            _max_lag_seconds = 0.0
            _healthy_since = None
            _breach_count = 0
    else:
        _warning_streak = 0


def _write_heartbeat(path: str) -> None:
    # Write-then-rename so a reader (the liveness probe) never sees a partial
    # file. The temporary path must be unique: rolling deployments, local dev,
    # or a slow process shutdown can briefly leave two worker processes sharing
    # the same heartbeat destination.
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            tmp_path = handle.name
            handle.write(str(int(time.time())))
        os.replace(tmp_path, destination)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass


async def loop_lag_watchdog(
    *,
    service_name: str = "lemma",
    heartbeat_path: str | None = None,
) -> None:
    """Background task: measure loop lag + refresh the liveness heartbeat.

    Also starts the stall sampler, which answers the question this loop cannot:
    the lag is measured *after* the loop is running again, so the blocking call
    is already gone by the time there is a number to report. The sampler watches
    from a thread and captures the culprit's stack during the stall.
    """
    interval = max(0.05, settings.loop_lag_watchdog_interval_seconds)
    warn = settings.loop_lag_warn_seconds
    sampler = start_loop_stall_sampler(
        stall_seconds=max(warn, settings.loop_stall_sample_seconds),
        service_name=service_name,
    )
    # The sampler reports a stall as "time since the loop last said it was
    # alive", so whatever publishes that tick sets the floor under every stall
    # it can measure. This loop is the wrong publisher on both counts: it ticks
    # once per `interval` (0.5s), and it does so *after* awaiting an offloaded
    # heartbeat write that queues behind the `cpu_bound` limiter. Ticking from
    # here added up to half a second to every stall, and on the worker could
    # report a stall with the loop perfectly healthy and merely eight offloads
    # deep. That is why loop lag improved and the stall count did not move.
    ticker = create_background_task(
        keep_loop_tick_fresh(sampler, max(0.001, settings.loop_stall_tick_seconds)),
        name="loop-stall-tick",
    )
    try:
        while True:
            scheduled_at = time.perf_counter()
            await asyncio.sleep(interval)
            lag = time.perf_counter() - scheduled_at - interval
            lag = max(0.0, lag)
            _lag.seconds = lag

            if heartbeat_path:
                try:
                    # Offloaded because it is real filesystem I/O — mkdir, a
                    # temp file, an atomic rename — on whatever volume the pod
                    # was given. Small, but the one thing in this process that
                    # must never be the reason the loop it measures stalls.
                    await run_blocking(_write_heartbeat, heartbeat_path, limiter="cpu_bound")
                except OSError as exc:  # pragma: no cover - defensive
                    logger.debug(
                        "runtime.heartbeat.write_failed",
                        error_type=type(exc).__name__,
                        service=service_name,
                    )

            _evaluate_lag(lag, warn, service_name=service_name)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        stop_loop_stall_sampler()
