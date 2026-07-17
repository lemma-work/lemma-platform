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
``runtime.loop_lag.degraded`` event fires on the transition. Recovery emits a
single ``runtime.loop_lag.recovered`` event only after a small hysteresis window
of consecutive healthy checks, so threshold jitter does not alternate events.

Mirrors the background-task shape of ``_consumer_group_reconcile_loop`` in the
streaq runtime: started in the lifespan, cancelled on shutdown.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import time

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Most-recent measured event-loop lag (seconds). Module-global so /health/live
# and metrics can read it without holding a reference to the task.
_last_lag_seconds: float = 0.0

# Degraded-state machine for loop-lag telemetry. Module-global so the watchdog
# task and tests can reset/inspect it without holding a reference to the task.
_degraded: bool = False
_degraded_since: float = 0.0  # time.monotonic() at degraded transition
_max_lag_seconds: float = 0.0  # peak lag observed during the current degraded window
_healthy_streak: int = 0  # consecutive healthy ticks while waiting to recover

# Consecutive healthy ticks required before emitting recovery. At the default
# 0.5s interval this is ~2.5s of sustained healthy loop time, enough to absorb
# threshold jitter without flapping degraded/recovered events.
_RECOVERY_HYSTERESIS_TICKS = 5


def get_loop_lag_seconds() -> float:
    return _last_lag_seconds


def is_loop_healthy() -> bool:
    """False when measured lag exceeds the unhealthy threshold (for /health/live)."""
    return _last_lag_seconds < settings.loop_lag_unhealthy_seconds


def reset_loop_watchdog_state() -> None:
    """Reset the degraded-state machine (for tests and process restart)."""
    global _degraded, _degraded_since, _max_lag_seconds, _healthy_streak, _last_lag_seconds
    _degraded = False
    _degraded_since = 0.0
    _max_lag_seconds = 0.0
    _healthy_streak = 0
    _last_lag_seconds = 0.0


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
    ``runtime.loop_lag.recovered`` once after ``_RECOVERY_HYSTERESIS_TICKS``
    consecutive healthy samples. ``now`` defaults to ``time.monotonic()`` and is
    overridable for tests so degraded-duration is deterministic.
    """
    global _degraded, _degraded_since, _max_lag_seconds, _healthy_streak
    clock = time.monotonic() if now is None else now
    if lag > warn:
        _healthy_streak = 0
        if not _degraded:
            _degraded = True
            _degraded_since = clock
            _max_lag_seconds = lag
            logger.warning(
                "runtime.loop_lag.degraded",
                lag_ms=round(lag * 1000, 1),
                threshold_ms=round(warn * 1000, 1),
                service=service_name,
            )
        elif lag > _max_lag_seconds:
            _max_lag_seconds = lag
    elif _degraded:
        _healthy_streak += 1
        if _healthy_streak >= _RECOVERY_HYSTERESIS_TICKS:
            degraded_duration_ms = round((clock - _degraded_since) * 1000, 1)
            logger.info(
                "runtime.loop_lag.recovered",
                max_lag_ms=round(_max_lag_seconds * 1000, 1),
                degraded_duration_ms=degraded_duration_ms,
                service=service_name,
            )
            _degraded = False
            _degraded_since = 0.0
            _max_lag_seconds = 0.0
            _healthy_streak = 0


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
    """Background task: measure loop lag + refresh the liveness heartbeat."""
    global _last_lag_seconds
    interval = max(0.05, settings.loop_lag_watchdog_interval_seconds)
    warn = settings.loop_lag_warn_seconds
    logger.info(
        "Loop-lag watchdog started",
        interval_seconds=interval,
        warn_seconds=warn,
        unhealthy_seconds=settings.loop_lag_unhealthy_seconds,
        heartbeat_path=heartbeat_path or None,
        service=service_name,
    )
    while True:
        scheduled_at = time.perf_counter()
        await asyncio.sleep(interval)
        lag = time.perf_counter() - scheduled_at - interval
        lag = max(0.0, lag)
        _last_lag_seconds = lag

        if heartbeat_path:
            try:
                _write_heartbeat(heartbeat_path)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed writing loop-watchdog heartbeat",
                    path=heartbeat_path,
                    error=str(exc),
                )

        _evaluate_lag(lag, warn, service_name=service_name)
