"""Signals: how a write anywhere tells a pod's hubs to look.

A signal is a small JSON object published on the pod's Redis Pub/Sub channel,
``pod:<id>:live``. It is a **hint**, never the data: a hub that hears one reads
what changed from where it is kept, and a hub that misses one reads it on its
idle poll, so losing a signal costs latency, never a change. Ephemeral events
-- typing, an answer being written -- ride the same channel and are simply gone
if missed, which is all they promise.

Every signal names its kind under ``"k"``; the rest is the kind's. A write
signals *after commit* (``signal_after_commit``): before, a hub could read the
old state and believe itself current.

Each API process listens once per pod some socket in it watches
(``PodSignals``); topics' hubs register handlers by kind.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Mapping
from uuid import UUID

from opentelemetry import metrics
from pydantic import JsonValue
from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident
from app.core.request_context import create_background_task

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
signals_dropped = meter.create_counter(
    "lemma.live.signals_dropped",
    description=(
        "Live signals not published because too many were already in flight; "
        "hubs read those changes on their idle poll."
    ),
)
_publish_incident = DependencyIncident("live.signal.publish", logger=logger)

#: A signal: its kind under ``"k"``, then the kind's fields.
type Signal = Mapping[str, JsonValue]
#: What a hub does with a signal of a kind it registered for.
type SignalHandler = Callable[[Signal], None]
#: What a hub does when its pod's signals may have been missed (the listener
#: reconnected, or fell behind): read everything it watches.
type CatchUp = Callable[[], None]

#: A publish never waits longer than this for Redis.
_PUBLISH_TIMEOUT_SECONDS = 0.25
#: Signals in flight per process. Past it a signal is dropped and counted.
MAX_PENDING_SIGNALS = 1000
#: Where a session keeps the signals it will publish once it commits.
_SESSION_KEY = "lemma_live_signals"

_pending: set[asyncio.Task[None]] = set()  # memory: bounded by MAX_PENDING_SIGNALS


def signal_channel(pod_id: UUID) -> str:
    return f"pod:{pod_id}:live"


async def _publish(pod_id: UUID, payloads: list[str]) -> None:
    try:
        # The pool, not the process's Pub/Sub listener: workers publish too,
        # and have no listener to lease.
        client = get_redis(url=settings.redis_url)
        for payload in payloads:
            await asyncio.wait_for(
                client.publish(signal_channel(pod_id), payload),
                timeout=_PUBLISH_TIMEOUT_SECONDS,
            )
    except (RedisError, OSError, TimeoutError) as exc:
        # The change is already stored; a hub that misses this reads it on its
        # next signal or its idle poll.
        _publish_incident.record_failure(error_type=type(exc).__name__)
    else:
        _publish_incident.record_success()


def publish_signals(pod_id: UUID, signals: list[Signal]) -> None:
    """Publish ``signals`` now, in the background. Best effort and bounded: at
    most ``MAX_PENDING_SIGNALS`` publishes in flight per process."""
    if not signals:
        return
    if len(_pending) >= MAX_PENDING_SIGNALS:
        signals_dropped.add(len(signals))
        return
    # Encoded here, off the publish task: a signal is a few ids.
    payloads = [json.dumps(dict(signal)) for signal in signals]
    task = create_background_task(_publish(pod_id, payloads), name="live-signal")
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def publish_signal(pod_id: UUID, signal: Signal) -> None:
    publish_signals(pod_id, [signal])


def signal_after_commit(session: AsyncSession, pod_id: UUID, signal: Signal) -> None:
    """Publish ``signal`` once ``session``'s transaction commits; never if it
    rolls back. The same signal twice in one transaction is published once."""
    sync = session.sync_session
    pending: dict[UUID, list[Signal]] = sync.info.setdefault(_SESSION_KEY, {})
    # Kept for the session's life rather than ``once``: a unit of work commits
    # more than once, and a ``once`` listener stays registered without firing
    # again, so listening again was a silent no-op and every signal after the
    # session's first commit was dropped.
    if not event.contains(sync, "after_commit", _flush):
        event.listen(sync, "after_commit", _flush)
        event.listen(sync, "after_rollback", _forget)
    queued = pending.setdefault(pod_id, [])
    if signal not in queued:
        queued.append(signal)


def _flush(sync: Session) -> None:
    pending: dict[UUID, list[Signal]] = sync.info.pop(_SESSION_KEY, None) or {}
    for pod_id, signals in pending.items():
        publish_signals(pod_id, signals)


def _forget(sync: Session) -> None:
    sync.info.pop(_SESSION_KEY, None)


# -- listening -----------------------------------------------------------------


class PodSignals:
    """One process's listener on one pod's signals, shared by its hubs."""

    def __init__(self, pod_id: UUID, channel: RealtimeChannel) -> None:
        self.pod_id = pod_id
        self._channel = channel
        self._handlers: dict[str, list[SignalHandler]] = {}
        self._catch_ups: list[CatchUp] = []
        self._task: asyncio.Task[None] | None = None

    def listen(
        self, kinds: list[str], handler: SignalHandler, catch_up: CatchUp
    ) -> None:
        for kind in kinds:
            self._handlers.setdefault(kind, []).append(handler)
        self._catch_ups.append(catch_up)
        if self._task is None or self._task.done():
            self._task = create_background_task(
                self._pump(), name=f"live-signals-{self.pod_id}"
            )

    def forget(self, handler: SignalHandler, catch_up: CatchUp) -> None:
        for handlers in self._handlers.values():
            with contextlib.suppress(ValueError):
                handlers.remove(handler)
        with contextlib.suppress(ValueError):
            self._catch_ups.remove(catch_up)

    @property
    def idle(self) -> bool:
        return not self._catch_ups

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _catch_up(self) -> None:
        for catch_up in list(self._catch_ups):
            catch_up()

    def _dispatch(self, raw: str | bytes) -> None:
        try:
            signal = json.loads(raw)
        except ValueError, TypeError:
            return
        if not isinstance(signal, dict):
            return
        for handler in list(self._handlers.get(str(signal.get("k")), ())):
            handler(signal)

    async def _pump(self) -> None:
        backoff = 0.25
        while True:
            try:
                async with self._channel.subscribe(
                    [signal_channel(self.pod_id)]
                ) as messages:
                    backoff = 0.25
                    # Anything signalled while we were not listening is read now.
                    self._catch_up()
                    async for message in messages:
                        self._dispatch(message)
            except asyncio.CancelledError:
                raise
            except RealtimeSlowConsumerError:
                self._catch_up()
            # RuntimeError: the process-wide Pub/Sub service stopped under us.
            except (RedisError, OSError, RuntimeError) as exc:
                logger.warning(
                    "live.signals_lost.degraded",
                    pod_id=str(self.pod_id),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)


class SignalListeners:
    """Every pod's listener in this process, made on first use."""

    def __init__(self) -> None:
        self._pods: dict[UUID, PodSignals] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def of(self, pod_id: UUID) -> PodSignals:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # A listener's task belongs to the loop that made it; a test that
            # started a fresh loop starts fresh listeners.
            self._pods, self._loop = {}, loop
        listener = self._pods.get(pod_id)
        if listener is None:
            listener = PodSignals(pod_id, await get_channel_service())
            self._pods[pod_id] = listener
        return listener

    async def release(self, pod_id: UUID) -> None:
        """Stop a pod's listener once no hub needs it."""
        listener = self._pods.get(pod_id)
        if listener is not None and listener.idle:
            self._pods.pop(pod_id, None)
            await listener.stop()


_listeners = SignalListeners()


def signal_listeners() -> SignalListeners:
    return _listeners


__all__ = [
    "CatchUp",
    "MAX_PENDING_SIGNALS",
    "PodSignals",
    "Signal",
    "SignalHandler",
    "SignalListeners",
    "publish_signal",
    "publish_signals",
    "signal_after_commit",
    "signal_channel",
    "signal_listeners",
]
