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

An organization has a channel of its own, ``org:<id>:live``, for what is about
the organization rather than one pod in it: a pod made or deleted, a member
added or taken out. The ``org`` topic's hub hears it and decides again which
pods each of its readers covers.
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


def org_signal_channel(organization_id: UUID) -> str:
    return f"org:{organization_id}:live"


async def _publish(channel: str, payloads: list[str]) -> None:
    try:
        # The pool, not the process's Pub/Sub listener: workers publish too,
        # and have no listener to lease.
        client = get_redis(url=settings.redis_url)
        for payload in payloads:
            await asyncio.wait_for(
                client.publish(channel, payload),
                timeout=_PUBLISH_TIMEOUT_SECONDS,
            )
    except (RedisError, OSError, TimeoutError) as exc:
        # The change is already stored; a hub that misses this reads it on its
        # next signal or its idle poll.
        _publish_incident.record_failure(error_type=type(exc).__name__)
    else:
        _publish_incident.record_success()


def _publish_on(channel: str, signals: list[Signal]) -> None:
    if not signals:
        return
    if len(_pending) >= MAX_PENDING_SIGNALS:
        signals_dropped.add(len(signals))
        return
    # Encoded here, off the publish task: a signal is a few ids.
    payloads = [json.dumps(dict(signal)) for signal in signals]
    task = create_background_task(_publish(channel, payloads), name="live-signal")
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def publish_signals(pod_id: UUID, signals: list[Signal]) -> None:
    """Publish ``signals`` now, in the background. Best effort and bounded: at
    most ``MAX_PENDING_SIGNALS`` publishes in flight per process."""
    _publish_on(signal_channel(pod_id), signals)


def publish_signal(pod_id: UUID, signal: Signal) -> None:
    publish_signals(pod_id, [signal])


def publish_org_signal(organization_id: UUID, signal: Signal) -> None:
    """Publish ``signal`` on the organization's channel, as ``publish_signal``
    does on a pod's."""
    _publish_on(org_signal_channel(organization_id), [signal])


def signal_after_commit(session: AsyncSession, pod_id: UUID, signal: Signal) -> None:
    """Publish ``signal`` once ``session``'s transaction commits; never if it
    rolls back. The same signal twice in one transaction is published once."""
    sync = session.sync_session
    pending: dict[UUID, list[Signal]] | None = sync.info.get(_SESSION_KEY)
    if pending is None:
        pending = {}
        sync.info[_SESSION_KEY] = pending
        event.listen(sync, "after_commit", _flush, once=True)
        event.listen(sync, "after_rollback", _forget, once=True)
    queued = pending.setdefault(pod_id, [])
    if signal not in queued:
        queued.append(signal)


def _flush(sync: Session) -> None:
    pending: dict[UUID, list[Signal]] = sync.info.pop(_SESSION_KEY, None) or {}
    if event.contains(sync, "after_rollback", _forget):
        event.remove(sync, "after_rollback", _forget)
    for pod_id, signals in pending.items():
        publish_signals(pod_id, signals)


def _forget(sync: Session) -> None:
    sync.info.pop(_SESSION_KEY, None)
    if event.contains(sync, "after_commit", _flush):
        event.remove(sync, "after_commit", _flush)


# -- listening -----------------------------------------------------------------


class PodSignals:
    """One process's listener on one pod's signals, shared by its hubs -- or on
    an organization's (``name``), for the ``org`` topic's."""

    def __init__(
        self, pod_id: UUID, channel: RealtimeChannel, *, name: str | None = None
    ) -> None:
        self.pod_id = pod_id
        self.name = name or signal_channel(pod_id)
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
                async with self._channel.subscribe([self.name]) as messages:
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
                    channel=self.name,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)


class SignalListeners:
    """Every pod's (and organization's) listener in this process, made on
    first use."""

    def __init__(self) -> None:
        self._pods: dict[str, PodSignals] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def of(self, pod_id: UUID) -> PodSignals:
        return await self._of(pod_id, signal_channel(pod_id))

    async def of_org(self, organization_id: UUID) -> PodSignals:
        return await self._of(organization_id, org_signal_channel(organization_id))

    async def _of(self, owner: UUID, name: str) -> PodSignals:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # A listener's task belongs to the loop that made it; a test that
            # started a fresh loop starts fresh listeners.
            self._pods, self._loop = {}, loop
        listener = self._pods.get(name)
        if listener is None:
            listener = PodSignals(owner, await get_channel_service(), name=name)
            self._pods[name] = listener
        return listener

    async def release(self, pod_id: UUID) -> None:
        """Stop a pod's listener once no hub needs it."""
        await self._release(signal_channel(pod_id))

    async def release_org(self, organization_id: UUID) -> None:
        await self._release(org_signal_channel(organization_id))

    async def _release(self, name: str) -> None:
        listener = self._pods.get(name)
        if listener is not None and listener.idle:
            self._pods.pop(name, None)
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
    "org_signal_channel",
    "publish_org_signal",
    "publish_signal",
    "publish_signals",
    "signal_after_commit",
    "signal_channel",
    "signal_listeners",
]
