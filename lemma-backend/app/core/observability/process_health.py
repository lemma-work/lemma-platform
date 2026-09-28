"""Whether this process still believes it is doing its job.

The loop watchdog's heartbeat file and the worker's ``lemma:worker:alive`` key
used to mean only "the event loop is turning". A worker whose stream reader
had died, or whose bulk lane had crashed, still turned its loop -- so both
signals stayed fresh for the hours it did nothing, and every probe and every
readiness check reading them said healthy.

Anything that finds this process unable to do its work records it here, once,
and both signals stop being refreshed. A liveness probe then restarts the
process; a readiness check reports the worker as stalled. There is no way back
to healthy within a process on purpose: what broke it (a task that ended, a
client whose connection pool was poisoned) is process state, and a restart is
the one repair that is guaranteed to clear it.
"""

from __future__ import annotations

_unhealthy_reason: str | None = None


def mark_process_unhealthy(reason: str) -> bool:
    """Record why this process can no longer do its work.

    Returns whether this call was the first -- the caller that gets ``True``
    owns reporting it and shutting the process down.
    """
    global _unhealthy_reason
    if _unhealthy_reason is not None:
        return False
    _unhealthy_reason = reason
    return True


def process_unhealthy_reason() -> str | None:
    """Why this process gave up, or ``None`` while it is healthy."""
    return _unhealthy_reason


def reset_process_health_for_tests() -> None:
    global _unhealthy_reason
    _unhealthy_reason = None
