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
  gets a liveness signal; the API additionally serves ``/livez``.

Mirrors the background-task shape of ``_consumer_group_reconcile_loop`` in the
streaq runtime: started in the lifespan, cancelled on shutdown.
"""

from __future__ import annotations

import asyncio
import os
import time

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Most-recent measured event-loop lag (seconds). Module-global so /livez and
# metrics can read it without holding a reference to the task.
_last_lag_seconds: float = 0.0


def get_loop_lag_seconds() -> float:
    return _last_lag_seconds


def is_loop_healthy() -> bool:
    """False when measured lag exceeds the unhealthy threshold (for /livez)."""
    return _last_lag_seconds < settings.loop_lag_unhealthy_seconds


def _write_heartbeat(path: str) -> None:
    # Write-then-rename so a reader (the liveness probe) never sees a partial
    # file. ~10 bytes to tmpfs — negligible on the loop.
    tmp = f"{path}.tmp"
    with open(tmp, "w") as handle:
        handle.write(str(int(time.time())))
    os.replace(tmp, path)


async def loop_lag_watchdog(*, service_name: str = "lemma") -> None:
    """Background task: measure loop lag + refresh the liveness heartbeat."""
    global _last_lag_seconds
    interval = max(0.05, settings.loop_lag_watchdog_interval_seconds)
    warn = settings.loop_lag_warn_seconds
    heartbeat_path = settings.worker_heartbeat_path
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
        _last_lag_seconds = max(0.0, lag)

        if heartbeat_path:
            try:
                _write_heartbeat(heartbeat_path)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed writing loop-watchdog heartbeat",
                    path=heartbeat_path,
                    error=str(exc),
                )

        if lag > warn:
            logger.warning(
                "Event loop lag high",
                lag_seconds=round(lag, 3),
                service=service_name,
            )
