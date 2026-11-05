"""A pod hub's start: the task that runs the hub, and what its waiters hear.

Every socket for a pod waits on its hub reading a starting position. Whatever
ends that start -- an outage, a bug, a stop -- has to reach those waiters, and
the wait is bounded, so a socket is refused rather than left hanging. A hub
whose task has ended starts again on the next socket instead of refusing the
pod until it is reaped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.request_context import create_background_task
from app.core.live.protocol import LiveUnavailable
from app.modules.datastore.domain.live import LiveCursorExpired

#: How long a socket waits for its pod's hub to read its starting position.
START_TIMEOUT = 10.0
#: Start failures a socket reports as themselves; anything else is
#: ``LiveUnavailable``.
_EXPECTED = (SQLAlchemyError, OSError, LiveCursorExpired)


class StartAttempt:
    """One start: its waiters wake on its event and read its own result, never
    a later attempt's -- and the run it launched settles it, not whichever
    attempt is current by the time that run gets there."""

    def __init__(self) -> None:
        self.done = asyncio.Event()
        self.error: BaseException | None = None

    def settle(self, error: BaseException | None) -> None:
        if not self.done.is_set():
            self.error = error
            self.done.set()

    def succeeded(self) -> None:
        self.settle(None)

    def failed(self, exc: BaseException) -> None:
        self.settle(
            exc
            if isinstance(exc, _EXPECTED)
            else LiveUnavailable("the pod's live feed did not start")
        )


class HubStart:
    def __init__(self, name: str) -> None:
        self._name = name
        self.task: asyncio.Task[None] | None = None
        self._attempt = StartAttempt()

    def launch(self, run: Callable[[StartAttempt], Coroutine[Any, Any, None]]) -> bool:
        """Run the hub unless it is running; True when it never ran before."""
        if self.task is not None and not self.task.done():
            return False
        first = self.task is None
        self._attempt = StartAttempt()
        self.task = create_background_task(run(self._attempt), name=self._name)
        return first

    async def until_started(self) -> None:
        attempt = self._attempt
        try:
            await asyncio.wait_for(attempt.done.wait(), timeout=START_TIMEOUT)
        except TimeoutError as exc:
            raise LiveUnavailable("the pod's live feed took too long to start") from exc
        if attempt.error is not None:
            raise attempt.error

    def take(self) -> asyncio.Task[None] | None:
        """Hand the task over to be stopped. A start it never got to make --
        cancelled before its first step -- is failed here, so its waiters hear
        now rather than at the timeout."""
        task, self.task = self.task, None
        self._attempt.settle(LiveUnavailable("the pod's live feed was stopped"))
        return task
