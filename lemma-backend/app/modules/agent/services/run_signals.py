"""Steer and stop, pushed to the run they are for instead of polled for.

A run used to ask the database before every graph node whether the person had
said anything (an UPDATE claiming queued messages, normally matching nothing),
and every second whether it had been stopped (a SELECT of its status). Both
answers are now pushed: the write that queues a message into a running run, or
marks it STOP_REQUESTED, signals ``run.control`` on the pod's live channel
(``core/live/signals``) once it commits, and the worker running that run hears
it.

A signal is a hint, never the state: the run still claims its messages and
reads its stop from the database, only when told to -- and on a slow backstop,
and whenever its listener reconnected (it may have missed one). A signal lost
costs latency, never a message or a stop; the completion backstop that answers
messages a run never claimed stays as it was.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live.signals import Signal, signal_after_commit, signal_listeners
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: The kind of signal a run's control rides under.
RUN_CONTROL = "run.control"
STEER = "steer"
STOP = "stop"


def signal_run(session: AsyncSession, *, pod_id: UUID, run_id: UUID, op: str) -> None:
    """Tell the worker running ``run_id`` to look, once ``session`` commits."""
    signal_after_commit(
        session, pod_id, {"k": RUN_CONTROL, "run": str(run_id), "op": op}
    )


class RunSignals:
    """What has been signalled to one run, and when to look again regardless."""

    def __init__(
        self,
        run_id: UUID,
        *,
        backstop_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run = str(run_id)
        self._backstop = backstop_seconds
        self._clock = clock
        # Look at once: whatever was queued before the run started listening.
        self._steer = True
        self._stop = False
        self._last_steer = -backstop_seconds
        self._last_stop = clock()

    def heard(self, signal: Signal) -> None:
        if signal.get("run") != self._run:
            return
        if signal.get("op") == STOP:
            self._stop = True
        elif signal.get("op") == STEER:
            self._steer = True

    def missed(self) -> None:
        """The listener reconnected or fell behind: look at both."""
        self._steer = True
        self._last_stop = -self._backstop

    def steer_due(self) -> bool:
        """Whether to claim what was sent to the run: told to, or overdue."""
        now = self._clock()
        if self._steer or now - self._last_steer >= self._backstop:
            self._steer = False
            self._last_steer = now
            return True
        return False

    def stop_signalled(self) -> bool:
        return self._stop

    def stop_check_due(self) -> bool:
        """Whether to read the run's stop flag: signalled, or overdue."""
        now = self._clock()
        if self._stop or now - self._last_stop >= self._backstop:
            self._last_stop = now
            return True
        return False

    @asynccontextmanager
    async def listening(self, pod_id: UUID) -> AsyncIterator[RunSignals]:
        """Hear the pod's run-control signals for as long as the run lasts.

        A listener that cannot start leaves the run on its backstop: told
        nothing, it looks every ``backstop_seconds``.
        """
        listeners = signal_listeners()
        try:
            pod = await listeners.of(pod_id)
            pod.listen([RUN_CONTROL], self.heard, self.missed)
        except RedisError, OSError, RuntimeError:
            logger.warning(
                "agent.run_signals.listen_failed.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )
            self._backstop = min(self._backstop, 1.0)
            yield self
            return
        try:
            yield self
        finally:
            pod.forget(self.heard, self.missed)
            await listeners.release(pod_id)


__all__ = ["RUN_CONTROL", "RunSignals", "STEER", "STOP", "signal_run"]
