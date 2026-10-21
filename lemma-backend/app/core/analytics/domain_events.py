"""Product analytics from committed domain events: an after-commit, batched sink.

Replaces the eleven ``analytics-*`` consumer groups. Those read every domain
stream through an inbox so that a projection of *committed* work could be
measured -- a durable path (outbox row, stream entry, inbox claim) for data that
is best effort by definition. Now ``SqlAlchemyUnitOfWork.commit`` hands its
collected events here after the commit succeeds, and one background task per
process projects them in batches:

* **after commit, never inside it.** A rolled-back transaction offers nothing,
  so an event that reaches a projection still means the change committed.
* **never blocks a commit.** ``offer`` appends to a bounded deque and returns;
  a full queue drops its *oldest* event and counts it
  (``lemma.analytics.sink.dropped``, plus a WARNING per batch that dropped).
* **batched.** The flusher wakes every ``interval`` or once ``batch`` events
  are waiting, and projects up to ``batch`` per round.
* **the same input as before.** A projection receives
  ``event.model_dump(mode="json")`` -- the dict the stream consumer received --
  under the event's own origin, the way the inbox bound it. The emitted
  analytics are pinned identical to the consumers' by
  ``app/core/tests/e2e/test_analytics_sink_parity_e2e.py``.

A process that crashes with events queued loses them. That is the trade the
design makes for analytics (design.md §10): no durable path, no inbox.

Projections register by event type (:func:`projection`) at import time, from
the analytics module's ``register_analytics`` thunk, which runs in every
process: the API commits most events, the worker the rest.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from opentelemetry import metrics
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.analytics.event_catalog import UnknownAnalyticEventError
from app.core.log.log import get_logger
from app.core.origin import origin_from_payload, origin_scope
from app.core.request_context import create_background_task

if TYPE_CHECKING:
    from app.core.domain.events import DomainEvent

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
dropped_counter = meter.create_counter("lemma.analytics.sink.dropped")
failed_counter = meter.create_counter("lemma.analytics.sink.failed")

Projection = Callable[[dict[str, Any]], Awaitable[None]]

DEFAULT_MAX_QUEUED = 10_000
DEFAULT_BATCH = 200
DEFAULT_INTERVAL_SECONDS = 2.0
#: One projection may read the database (run scope, member count, the delivery
#: claim); none may hold the flusher hostage.
_PROJECTION_TIMEOUT_SECONDS = 10.0
_CLOSE_TIMEOUT_SECONDS = 10.0

#: What a projection's body can raise and still be an outcome rather than a
#: bug: an event it cannot parse, a scope or delivery-claim read that failed
#: (database, Redis, a refused connection, its timeout), and a strict-mode
#: contract violation. Anything else is a bug and ends the flusher task loudly
#: (``create_background_task`` logs it); the next offer starts a new one.
_PROJECTION_ERRORS: tuple[type[BaseException], ...] = (
    ValidationError,
    SQLAlchemyError,
    RedisError,
    OSError,
    UnknownAnalyticEventError,
)

_projections: dict[str, list[Projection]] = {}  # memory: bounded one per event type


def projection(*event_types: str) -> Callable[[Projection], Projection]:
    """Register ``fn`` to project every committed event of ``event_types``."""

    def register(fn: Projection) -> Projection:
        for event_type in event_types:
            handlers = _projections.setdefault(event_type, [])
            if fn not in handlers:
                handlers.append(fn)
        return fn

    return register


def projected_event_types() -> frozenset[str]:
    return frozenset(_projections)


class DomainEventSink:
    """Bounded queue + one flusher task, bound to the loop that started it."""

    def __init__(
        self,
        *,
        max_queued: int = DEFAULT_MAX_QUEUED,
        batch: int = DEFAULT_BATCH,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._queue: deque[DomainEvent] = deque(maxlen=max_queued)
        self._batch = batch
        self._interval = interval_seconds
        self._dropped = 0
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        #: Held while a batch is projected, so ``flush`` waits for one in flight.
        self._busy = asyncio.Lock()
        self._stopping = False

    @property
    def dropped(self) -> int:
        return self._dropped

    def offer(self, events: Sequence[DomainEvent]) -> None:
        """Queue the events some projection wants. Never waits."""
        wanted = [e for e in events if e.event_type in _projections]
        if not wanted:
            return
        for event in wanted:
            if len(self._queue) == self._queue.maxlen:
                self._dropped += 1
                dropped_counter.add(1)
            self._queue.append(event)
        try:
            self._ensure_started()
        except RuntimeError:
            # A loop shutting down refuses new tasks: what is queued is
            # projected by the next process-wide flush (``aclose``).
            logger.warning("analytics.sink.offer_failed", exc_info=True)
            return
        if self._wake is not None and len(self._queue) >= self._batch:
            self._wake.set()

    def _ensure_started(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # flushed by the next offer made from a loop
        if self._task is not None and not self._task.done() and self._loop is loop:
            return
        self._loop = loop
        self._stopping = False
        self._wake = asyncio.Event()
        self._busy = asyncio.Lock()
        self._task = create_background_task(self._run(), name="analytics-domain-events")

    async def _run(self) -> None:
        assert self._wake is not None
        while True:
            async with self._busy:
                await self._drain(limit=self._batch)
            if not self._queue and self._stopping:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except TimeoutError:
                pass
            self._wake.clear()

    async def _drain(self, *, limit: int | None) -> int:
        projected = 0
        dropped_before = self._dropped
        while self._queue and (limit is None or projected < limit):
            event = self._queue.popleft()
            await self._project(event)
            projected += 1
        if self._dropped > dropped_before:
            logger.warning(
                "analytics.sink.dropped",
                dropped=self._dropped - dropped_before,
                dropped_total=self._dropped,
            )
        return projected

    async def _project(self, event: DomainEvent) -> None:
        handlers = _projections.get(event.event_type, ())
        payload: dict[str, Any] | None = None
        for handler in handlers:
            try:
                if payload is None:
                    payload = event.model_dump(mode="json")
                # The event's origin, as the inbox bound it: a projection that
                # raises its own events inherits how the work arrived.
                with origin_scope(origin_from_payload(payload)):
                    await asyncio.wait_for(
                        handler(payload), timeout=_PROJECTION_TIMEOUT_SECONDS
                    )
            except _PROJECTION_ERRORS:
                failed_counter.add(1)
                logger.warning(
                    "analytics.sink.projection_failed",
                    event_type=event.event_type,
                    exc_info=True,
                )

    async def flush(self) -> None:
        """Project everything queued now, on the caller's task (tests, shutdown)."""
        async with self._busy:
            await self._drain(limit=None)

    async def aclose(self) -> None:
        """Drain what is queued, bounded, then stop the flusher."""
        task = self._task
        self._stopping = True
        if task is None or task.done() or self._loop is not asyncio.get_running_loop():
            await self._drain(limit=None)
            return
        if self._wake is not None:
            self._wake.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_CLOSE_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                "analytics.sink.close_timed_out", still_queued=len(self._queue)
            )
            task.cancel()
        self._task = None


_sink = DomainEventSink()


def domain_event_sink() -> DomainEventSink:
    return _sink


def offer_committed(events: Sequence[DomainEvent]) -> None:
    """Called by the unit of work after a successful commit."""
    if events:
        _sink.offer(events)
