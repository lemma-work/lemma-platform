"""The per-process channels hub: one reader of a pod's channels for every socket.

Each API process keeps at most one hub per pod that some ``channel`` or
``inbox`` subscription in it watches. The hub hears the pod's signals
(``app/core/live/signals.py``): ``channel`` (a channel's change counter
moved), ``read`` (a member's read position moved), and the ephemeral
``typing`` and ``answering``. It reads what moved once per batch -- the
channel's head, the lines changed since the hub's own cursor for it, the
names they need -- and projects each line once per kind of reader, so database
work grows with watched channels and their changes, never with sockets.

Nothing is lost to a missed signal. A channel's changes are numbered under its
row lock and logged (``infrastructure.revisions``), and the hub reads the log
past its own ``rev``; an idle poll re-reads every watched head, the pod's
channels with a change logged since the last poll, and read positions moved
since then. The hub's cursors move only
once every subscription was offered what lies before them (``timeline``).

Access is decided once per subscription and again on the hub's
``AccessClock`` (``app/core/live/access_clock.py``).

A subscription joins at the hub's cursor for its channel. One that resumes
(``since``) is first sent its own gap, read directly under the hub's lock so
the live fan-out cannot interleave with it. A line may then reach it twice --
in its gap and again in the next batch -- which is harmless: every event
carries the whole line, and clients apply events as upserts by id.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.live.access_clock import AccessClock
from app.core.live.config import LiveSettings
from app.core.live.connection import LiveConnection
from app.core.live.protocol import (
    LiveAccessDenied,
    LiveErrorCode,
    LiveUnavailable,
    ResyncReason,
    UnsubscribedReason,
)
from app.core.live.signals import Signal, SignalListeners
from app.core.live.topics import LiveRegistry
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.modules.channels.api.access import Caller
from app.modules.channels.domain.entities import Channel
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.services.live.answers import (
    Answers,
    answering_for,
    answering_of,
    as_uuid,
)
from app.modules.channels.services.live.events import Head, heads_of, typing_event
from app.modules.channels.services.live.inbox_feed import InboxFeed
from app.modules.channels.services.live.instruments import (
    backoff,
    hub_drain_seconds,
    hub_reads,
    hub_restarts,
    hub_resyncs,
)
from app.modules.channels.services.live.readers import visible_channel
from app.modules.channels.services.live.subscriptions import (
    ChannelSub,
    InboxSocket,
    InboxSub,
)
from app.modules.channels.services.live.timeline import (
    gap_since,
    plan_changes,
    read_updates,
    readers_now,
    reads_moved,
)

logger = get_logger(__name__)
EpochReader = Callable[[UUID], Awaitable[str | None]]

#: The signals the hub hears.
SIGNAL_KINDS = ["channel", "read", "typing", "answering"]
#: How far back a poll looks for channels that moved: a change is stamped when
#: it is written, which is before its transaction commits; the signal covers a
#: transaction longer than this.
_CHANNEL_OVERLAP = timedelta(seconds=30)


class ChannelsHub:
    def __init__(
        self,
        pod_id: UUID,
        *,
        uow_factory: UnitOfWorkFactory,
        listeners: SignalListeners,
        streams: LiveRegistry,
        epoch_of: EpochReader,
        settings: LiveSettings,
        on_idle: Callable[["ChannelsHub"], None],
        backoff: Callable[[int], float] = backoff,
    ) -> None:
        self.pod_id = pod_id
        self._uow_factory = uow_factory
        self._listeners = listeners
        self._epoch_of = epoch_of
        self._settings = settings
        self._on_idle = on_idle
        self._backoff = backoff
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._channels: dict[UUID, set[ChannelSub]] = {}
        #: The hub's cursor per watched channel.
        self._heads: dict[UUID, Head] = {}
        self.inbox = InboxFeed(pod_id)
        self._dirty: set[UUID] = set()
        self._reads: set[tuple[UUID, UUID]] = set()
        self._everything = False
        self._joining = 0
        self._task: asyncio.Task[None] | None = None
        #: The first subscriber's start, which every concurrent one awaits.
        self._starting: asyncio.Task[None] | None = None
        #: Unforeseen ends of the loop since it last read cleanly.
        self._loop_failures = 0
        self._started = False
        self._stopped = False
        self.access = AccessClock(settings)
        self._next_poll = 0.0
        self._last_read = 0.0
        #: The database's clock at the last poll: what the next one reads from.
        self._polled_at: datetime | None = None
        self.idle_since: float | None = None
        self._answers = Answers(
            deliver=self._deliver_to,
            streams=streams,
            longest=settings.live_answer_stream_seconds,
        )

    # -- lifecycle ------------------------------------------------------------

    async def _ensure_started(self) -> None:
        """Start the hub once: one start task, made before anything is awaited,
        that every concurrent first subscriber awaits -- two cannot each start
        a loop and orphan one. A failed start is made again by the next."""
        if self._stopped:
            raise LiveUnavailable("the pod's channels feed is stopping")
        if self._started:
            if self._task is None or self._task.done():
                # Its restart was lost (it is scheduled from the loop's end):
                # the next subscriber starts it again.
                self._start_loop()
            return
        if self._starting is None or self._starting.done():
            self._starting = create_background_task(
                self._start(), name=f"live-channels-start-{self.pod_id}"
            )
        starting = self._starting
        try:
            # Shielded: one subscriber giving up does not cancel the others'.
            await asyncio.shield(starting)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if starting.cancelled() and not (current and current.cancelling()):
                # The start was cancelled by ``stop``, not this subscriber.
                raise LiveUnavailable("the pod's channels feed stopped") from None
            raise
        if not self._started:
            raise LiveUnavailable("the pod's channels feed is stopping")

    async def _start(self) -> None:
        listener = await self._listeners.of(self.pod_id)
        epoch = await self._epoch_of(self.pod_id)
        if self._stopped or self._started:
            return
        listener.listen(SIGNAL_KINDS, self.on_signal, self.catch_up)
        self.access.start(epoch)
        self._next_poll = time.monotonic() + self._settings.live_idle_poll_seconds
        self._started = True
        self._start_loop()

    def _start_loop(self, *, after: float = 0.0) -> None:
        self._task = create_background_task(
            self._run(after=after), name=f"live-channels-{self.pod_id}"
        )
        self._task.add_done_callback(self._loop_ended)

    def _loop_ended(self, task: asyncio.Task[None]) -> None:
        """The loop met an error nobody foresaw -- a bug, not an outage. It is
        started again after a backoff rather than leave every socket of the
        pod in this process silent until a new subscriber happens to come; the
        backoff keeps a fault that repeats from spinning."""
        if task.cancelled() or not self._started or task is not self._task:
            return
        fault = task.exception()
        if fault is None:
            return
        self._loop_failures += 1
        hub_restarts.add(1)
        logger.error(
            "channels.live.hub_loop_failed",
            pod_id=str(self.pod_id),
            error_type=type(fault).__name__,
            failures=self._loop_failures,
            exc_info=fault,
        )
        self._start_loop(after=self._backoff(self._loop_failures))

    async def stop(self) -> None:
        self._stopped = True
        self._started = False
        starting, self._starting = self._starting, None
        if starting is not None and not starting.done():
            starting.cancel()
            # Waited on, not awaited: how the start ended is its subscribers'.
            await asyncio.wait({starting})
        task, self._task = self._task, None
        listener = await self._listeners.of(self.pod_id)
        listener.forget(self.on_signal, self.catch_up)
        await self._listeners.release(self.pod_id)
        await self._answers.close()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def subscription_count(self) -> int:
        return sum(len(subs) for subs in self._channels.values()) + sum(
            len(subs) for subs in self.inbox.subs.values()
        )

    def _maybe_idle(self) -> None:
        if (
            not self._channels
            and not self.inbox
            and not self._joining
            and self.idle_since is None
        ):
            self.idle_since = time.monotonic()
            self._on_idle(self)

    # -- joining ----------------------------------------------------------------

    async def join_channel(
        self,
        connection: LiveConnection,
        subscription_id: str,
        *,
        caller: Caller,
        channel_id: UUID,
        since: str | None,
    ) -> None:
        """Index a ``channel`` subscription, from ``since`` then its gap.

        Visibility is decided under the lock, after the head is read: a
        removal committing later takes a change past that head, which the next
        read ends the subscription on, rather than landing behind it unseen."""
        self._joining += 1
        self.idle_since = None
        try:
            await self._ensure_started()
            async with self._lock, self._uow_factory() as uow:
                if connection.closed:
                    return
                head = self._heads.get(channel_id)
                if head is None:
                    head = (await heads_of(uow, [channel_id])).get(channel_id)
                    if head is None:
                        raise LiveAccessDenied(
                            LiveErrorCode.NOT_FOUND, "Channel not found"
                        )
                channel = await visible_channel(uow, caller, channel_id)
                self._heads.setdefault(channel.id, head)
                sub = ChannelSub(
                    id=subscription_id,
                    connection=connection,
                    channel_id=channel.id,
                    caller=caller,
                    cursor=head.cursor,
                    on_release=self._release_channel,
                )
                connection.subscriptions[subscription_id] = sub
                await self._send_gap(uow, sub, channel, head, since)
                if sub.active:
                    # Nothing awaited from here: a delta relayed now reaches
                    # it after the snapshot, never before.
                    self._channels.setdefault(channel.id, set()).add(sub)
                    caught_up = self._answers.so_far(channel.id, sub.user_id)
                    if caught_up:
                        connection.offer_events(sub.id, sub.cursor, caught_up)
        finally:
            self._joining -= 1
            self._maybe_idle()

    async def _send_gap(
        self,
        uow: SqlAlchemyUnitOfWork,
        sub: ChannelSub,
        channel: Channel,
        head: Head,
        since: str | None,
    ) -> None:
        """Say it is subscribed -- at ``since``, where a client dropping before
        its gap resumes, or at the head it must resync to -- then the gap."""
        if since is None:
            sub.connection.offer_subscribed(sub.id, head.cursor)
            return
        limit = self._settings.live_max_changes_per_batch
        gap = await gap_since(uow, sub, channel, head, since, limit=limit)
        resync = isinstance(gap, ResyncReason)
        sub.connection.offer_subscribed(sub.id, head.cursor if resync else since)
        if isinstance(gap, ResyncReason):
            self._resync(sub, gap)
        else:
            sub.connection.offer_events(sub.id, head.cursor, gap, previous_cursor=since)

    async def join_inbox(
        self, connection: InboxSocket, subscription_id: str, *, caller: Caller
    ) -> InboxSub:
        self._joining += 1
        self.idle_since = None
        try:
            await self._ensure_started()
            async with self._lock, self._uow_factory() as uow:
                sub = InboxSub(
                    id=subscription_id,
                    connection=connection,
                    caller=caller,
                    on_release=self._release_inbox,
                )
                connection.subscriptions[subscription_id] = sub
                if not connection.closed:  # A failed join is an error, not subscribed.
                    await self.inbox.join(uow, sub)
                connection.offer_subscribed(subscription_id, None)
                if not connection.closed:
                    # Their answers are followed from now, so the channel
                    # they open next is sent each whole.
                    self._answers.follow_for(sub.user_id)
                return sub
        finally:
            self._joining -= 1
            self._maybe_idle()

    def _release_channel(self, sub: ChannelSub) -> None:
        subs = self._channels.get(sub.channel_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                self._channels.pop(sub.channel_id, None)
                self._heads.pop(sub.channel_id, None)
        self._maybe_idle()

    def _release_inbox(self, sub: InboxSub) -> None:
        self.inbox.release(sub)
        self._maybe_idle()

    # -- signals ----------------------------------------------------------------

    def on_signal(self, signal: Signal) -> None:
        kind = signal.get("k")
        channel_id = as_uuid(signal.get("c"))
        if channel_id is None:
            return
        if kind == "channel":
            self._dirty.add(channel_id)
            self._wake.set()
        elif kind == "read":
            user_id = as_uuid(signal.get("u"))
            if user_id is not None:
                self._reads.add((channel_id, user_id))
                self._wake.set()
        elif kind == "typing":
            self._on_typing(channel_id, signal)
        elif kind == "answering":
            self._on_answering(channel_id, signal)

    def catch_up(self) -> None:
        """Signals may have been missed: compare everything watched."""
        self._everything = True
        self._wake.set()

    def _on_typing(self, channel_id: UUID, signal: Signal) -> None:
        typist = as_uuid(signal.get("u"))
        event = typing_event(channel_id, signal.get("who"), signal.get("t"))
        for sub in list(self._channels.get(channel_id, ())):
            if sub.user_id != typist:
                sub.connection.offer_events(sub.id, sub.cursor, [event])

    def _on_answering(self, channel_id: UUID, signal: Signal) -> None:
        answering = answering_of(channel_id, signal)
        if answering is None:
            return
        state = "started" if signal.get("state") == "started" else "finished"
        for sub in list(self._channels.get(channel_id, ())):
            sub.connection.offer_events(
                sub.id, sub.cursor, [answering_for(state, answering, sub.user_id)]
            )
        if state == "finished":
            self._answers.finished(answering)
            return
        self._answers.started(answering)
        if self._has_reader(answering.for_user_id):
            self._answers.follow(answering)

    def _has_reader(self, user_id: UUID) -> bool:
        return user_id in self.inbox.subs or any(
            sub.user_id == user_id for subs in self._channels.values() for sub in subs
        )

    def _deliver_to(self, channel_id: UUID, user_id: UUID, event: str) -> None:
        for sub in list(self._channels.get(channel_id, ())):
            if sub.user_id == user_id:
                sub.connection.offer_events(sub.id, sub.cursor, [event])

    # -- the loop ---------------------------------------------------------------

    async def _run(self, *, after: float = 0.0) -> None:
        if after:
            await asyncio.sleep(after)
            # What was missed while it was down is found by comparing all.
            self._everything = True
        failures = 0
        while True:
            polled = await self._wait()
            started = time.monotonic()
            try:
                if time.monotonic() >= self.access.next_check:
                    await self._check_access()
                async with self._lock, self._uow_factory() as uow:
                    await self._drain(uow, polled=polled)
            except (SQLAlchemyError, RedisError, OSError, TimeoutError) as exc:
                # What was not read is read on the next wake or poll: the
                # hub's cursors only move past what it sent.
                failures += 1
                logger.error(
                    "channels.live.hub_drain_failed",
                    pod_id=str(self.pod_id),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
            else:
                failures = self._loop_failures = 0
                hub_drain_seconds.record(time.monotonic() - started)
                continue
            self._everything = True
            await asyncio.sleep(self._backoff(failures))

    async def _wait(self) -> bool:
        """Sleep until there is something to read; whether a poll is due."""
        timeout = max(
            0.0, min(self._next_poll, self.access.next_check) - time.monotonic()
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        # Reads are a coalescing window apart: signals landing in the window
        # ride one read.
        gap = self._last_read + self._settings.live_wake_coalesce_ms / 1000
        if gap > time.monotonic():
            await asyncio.sleep(gap - time.monotonic())
        self._wake.clear()
        self._last_read = time.monotonic()
        if self._last_read >= self._next_poll:
            self._next_poll = self._last_read + self._settings.live_idle_poll_seconds
            return True
        return False

    async def _drain(self, uow: SqlAlchemyUnitOfWork, *, polled: bool) -> None:
        everything = self._everything
        self._everything = False
        dirty, self._dirty = self._dirty, set()
        reads, self._reads = self._reads, set()
        polled_at = self._polled_at
        read = False
        try:
            await self._read(
                uow, dirty=dirty, reads=reads, polled=polled, everything=everything
            )
            read = True
        finally:
            if not read:
                # Nothing taken for this read is dropped: the next one reads
                # it all again, from the same poll.
                self._dirty |= dirty
                self._reads |= reads
                self._polled_at = polled_at
                self._everything = self._everything or everything

    async def _read(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        dirty: set[UUID],
        reads: set[tuple[UUID, UUID]],
        polled: bool,
        everything: bool,
    ) -> None:
        hub_reads.add(1)
        changed_since: datetime | None = None
        if polled or everything:
            dirty = dirty | set(self._channels)
            # The database's clock, which stamped the rows, not this process's.
            now = await uow.session.scalar(select(func.now()))
            since, self._polled_at = self._polled_at, now
            if since is None:
                # The first poll: the inbox compares every channel once.
                everything = True
            else:
                users = {s.user_id for subs in self._channels.values() for s in subs}
                reads = reads | await reads_moved(uow, users | self.inbox.users, since)
                changed_since = since - _CHANNEL_OVERLAP
        watched = dirty & set(self._channels)
        heads = await heads_of(uow, watched)
        for channel_id in sorted(watched):
            await self._advance(uow, channel_id, heads.get(channel_id))
        await self.inbox.drain(
            uow,
            dirty=dirty,
            reads=reads,
            everything=everything,
            changed_since=changed_since,
        )
        for sub, event in await read_updates(uow, reads, self._channels):
            sub.connection.offer_events(sub.id, sub.cursor, [event])

    async def _advance(
        self, uow: SqlAlchemyUnitOfWork, channel_id: UUID, head: Head | None
    ) -> None:
        """Send one channel's changes since the hub's cursor for it, up to
        ``head`` (``None``: the channel is gone). The cursor moves only once
        every subscriber was offered its events (``timeline``)."""
        subs = [sub for sub in self._channels.get(channel_id, ()) if sub.active]
        known = self._heads.get(channel_id)
        if known is not None and head is not None and head.rev <= known.rev:
            return
        channel = await ChannelRepository(uow.session).get(channel_id)
        if head is None or channel is None:
            for sub in subs:
                self._end(sub, UnsubscribedReason.GONE.value)
            return
        plan = await plan_changes(
            uow,
            channel,
            subs,
            since=known or Head(0, 0),
            head=head,
            limit=self._settings.live_max_changes_per_batch,
        )
        # Nothing awaited from here: every subscriber is offered its events,
        # and only then does the hub's cursor move.
        for sub in plan.revoked:
            self._end(sub, UnsubscribedReason.ACCESS_REVOKED.value)
        for sub, events in plan.sends:
            previous, sub.cursor = sub.cursor, head.cursor
            if events is None:
                self._resync(sub, ResyncReason.TOO_MANY)
            else:
                sub.connection.offer_events(
                    sub.id, head.cursor, events, previous_cursor=previous
                )
        if channel_id in self._channels:
            self._heads[channel_id] = head

    # -- authorization ------------------------------------------------------------

    async def _check_access(self) -> None:
        await self.access.check(lambda: self._epoch_of(self.pod_id), self._recheck_all)

    async def _recheck_all(self) -> None:
        async with self._lock, self._uow_factory() as uow:
            await self._recheck(uow)
            await self.inbox.recheck(uow, self._end)

    async def _recheck(self, uow: SqlAlchemyUnitOfWork) -> None:
        """Rebuild every reader, and end what they may no longer see."""
        revoked, kept = await readers_now(uow, self.pod_id, self._channels)
        for sub in revoked:
            self._end(sub, UnsubscribedReason.ACCESS_REVOKED.value)
        for sub, caller in kept:
            sub.caller = caller

    def _end(self, sub: ChannelSub | InboxSub, reason: str) -> None:
        sub.connection.offer_unsubscribed(sub.id, reason)

    def _resync(self, sub: ChannelSub, reason: ResyncReason) -> None:
        hub_resyncs.add(1, {"reason": reason.value, "topic": "channel"})
        sub.connection.offer_resync(sub.id, sub.cursor, reason)


__all__ = ["ChannelsHub", "EpochReader", "SIGNAL_KINDS"]
