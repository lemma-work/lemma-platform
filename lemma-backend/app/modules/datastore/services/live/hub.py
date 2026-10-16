"""The per-process pod hub: one reader of a pod's change log for every socket.

Each API process keeps at most one hub per pod that some socket in it watches.
The hub holds one Redis Pub/Sub subscription to ``pod:<id>:changes`` (a wake,
nothing more), reads the pod's log once per wake from its own cursor, fetches
the current rows once per ``(table, batch)``, and fans the result out in
memory. Database work is therefore proportional to watched pods and their
writes, never to sockets.

A subscription joins the hub's fan-out at the hub's cursor. One that resumes
(``since``) first reads its own gap from the log directly, under the hub's lock
so the live fan-out cannot interleave with it, and is then brought level with
the hub.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from opentelemetry import metrics
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.modules.datastore.config_live import LiveSettings
from app.modules.datastore.domain.live import (
    ChangePosition,
    LiveAccessDenied,
    LiveCursorExpired,
    LiveErrorCode,
    LivePage,
    LiveTable,
    MergedChange,
    ResyncReason,
    SubscribeRequest,
    UnsubscribedReason,
    merge_change,
)
from app.modules.datastore.domain.ports import LiveAccessPort, LiveChangeFeedPort
from app.modules.datastore.services.live.connection import (
    LiveConnection,
    LiveSubscription,
)
from app.modules.datastore.services.live.fanout import (
    HubStats,
    encode_batch,
    route_change,
)

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
hub_reads = meter.create_counter("lemma.live.hub_reads")
hub_resyncs = meter.create_counter("lemma.live.resyncs")
active_hubs = meter.create_up_down_counter("lemma.live.hubs")

#: Rows per log read. Matches the change log's own page size.
_PAGE = 500
#: Pages read before the hub flushes what it has, so an endless burst still
#: reaches clients while the hub works through it. Memory stays bounded either
#: way: a subscription holds at most ``live_max_changes_per_batch`` entries
#: before it is switched to a resync.
_FLUSH_PAGES = 40
#: Pages a resuming subscription may read for its own gap before it is told to
#: resync instead. The gap read is filtered to its tables but not to its rows.
_CATCH_UP_PAGES = 4
#: After a wake whose read found nothing (an older transaction still open holds
#: the readable horizon back), re-read after this, doubling up to the ceiling.
_RECHECK_FIRST = 0.1
_RECHECK_LAST = 1.6


def wake_channel(pod_id: UUID) -> str:
    return f"pod:{pod_id}:changes"


EpochReader = Callable[[UUID], Awaitable[str | None]]


class PodHub:
    def __init__(
        self,
        pod_id: UUID,
        *,
        feed: LiveChangeFeedPort,
        access: LiveAccessPort,
        channel: RealtimeChannel,
        epoch_of: EpochReader,
        settings: LiveSettings,
        on_idle: Callable[["PodHub"], None],
    ) -> None:
        self.pod_id = pod_id
        self._feed = feed
        self._access = access
        self._channel = channel
        self._epoch_of = epoch_of
        self._settings = settings
        self._on_idle = on_idle
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._by_table: dict[UUID, set[LiveSubscription]] = {}
        self._connections: set[LiveConnection] = set()
        self._cursor: str | None = None
        self._position: ChangePosition | None = None
        self._epoch: str | None = None
        self._next_epoch_check = 0.0
        self._recheck_at: float | None = None
        self._last_read = 0.0
        self._recheck_step = _RECHECK_FIRST
        self._next_poll = 0.0
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._start_error: BaseException | None = None
        self._idle_since: float | None = None
        self.stats = HubStats()

    # -- lifecycle ------------------------------------------------------------

    @property
    def cursor(self) -> str | None:
        return self._cursor

    @property
    def idle_since(self) -> float | None:
        return self._idle_since

    def subscription_count(self) -> int:
        return sum(len(subs) for subs in self._by_table.values())

    async def ensure_started(self) -> None:
        if self._task is None:
            self._task = create_background_task(
                self._run(), name=f"live-hub-{self.pod_id}"
            )
            active_hubs.add(1)
        await self._started.wait()
        if self._start_error is not None:
            raise self._start_error

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        active_hubs.add(-1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def attach(self, connection: LiveConnection) -> None:
        self._connections.add(connection)
        self._idle_since = None

    def detach(self, connection: LiveConnection) -> None:
        for subscription in list(connection.subscriptions.values()):
            self._unindex(subscription)
        self._connections.discard(connection)
        self._maybe_idle()

    def wake(self) -> None:
        self._wake.set()

    # -- subscribe / unsubscribe ---------------------------------------------

    async def subscribe(
        self,
        connection: LiveConnection,
        request: SubscribeRequest,
        *,
        max_subscriptions: int,
    ) -> None:
        """Check access, catch the subscription up, and join it to the fan-out."""
        if request.id in connection.subscriptions:
            connection.offer_error(
                request.id, LiveErrorCode.INVALID, "Subscription id already in use."
            )
            return
        if len(connection.subscriptions) >= max_subscriptions:
            connection.offer_error(
                request.id,
                LiveErrorCode.LIMIT,
                f"At most {max_subscriptions} subscriptions per socket.",
            )
            return
        try:
            tables = await self._tables_for(connection.user_id, request.table)
        except LiveAccessDenied as denied:
            connection.offer_error(request.id, denied.code, denied.message)
            return
        await self.ensure_started()
        async with self._lock:
            if connection.closed:
                return
            if not self._by_table:
                # Nobody was watching, so the hub's cursor stood still; joining
                # there would replay changes from before anyone subscribed.
                await self._move_to_horizon()
            assert self._cursor is not None and self._position is not None
            subscription = LiveSubscription(
                id=request.id,
                connection=connection,
                tables={table.id: table.name for table in tables},
                all_tables=request.table is None,
                ops=frozenset(request.ops) if request.ops else None,
                position=self._position,
                cursor=self._cursor,
                when=request.conditions(),
            )
            connection.subscriptions[request.id] = subscription
            await self._join(subscription, request.since)
            self._index(subscription)

    def unsubscribe(self, connection: LiveConnection, subscription_id: str) -> bool:
        subscription = connection.subscriptions.pop(subscription_id, None)
        if subscription is None:
            return False
        subscription.active = False
        self._unindex(subscription)
        return True

    async def _tables_for(
        self, user_id: UUID, table_name: str | None
    ) -> list[LiveTable]:
        if table_name is not None:
            return [await self._access.table(user_id, self.pod_id, table_name)]
        return await self._access.readable_tables(user_id, self.pod_id)

    async def _join(self, subscription: LiveSubscription, since: str | None) -> None:
        """Bring a new subscription level with the hub. Runs under the lock."""
        connection = subscription.connection
        cursor = self._cursor
        assert cursor is not None and self._position is not None
        if since is None:
            connection.offer_subscribed(subscription, cursor)
            return
        try:
            since_position = self._feed.position(since)
        except LiveCursorExpired:
            connection.offer_subscribed(subscription, cursor)
            self._resync(subscription, cursor, ResyncReason.CURSOR_EXPIRED)
            return
        if since_position >= self._position:
            # Resuming from a cursor another process's hub had already reached.
            subscription.advance(since_position, since)
            connection.offer_subscribed(subscription, since)
            return
        connection.offer_subscribed(subscription, cursor)
        try:
            gap = await self._read_gap(subscription, since)
        except LiveCursorExpired:
            self._resync(subscription, cursor, ResyncReason.CURSOR_EXPIRED)
            return
        if gap is None:
            self._resync(subscription, cursor, ResyncReason.TOO_MANY)
            return
        if gap:
            encoded = await encode_batch(
                self._feed, self.pod_id, {subscription: gap}, self.stats
            )
            connection.offer_changes(
                subscription,
                cursor,
                encoded.get(subscription, []),
                previous_cursor=since,
            )

    async def _read_gap(
        self, subscription: LiveSubscription, since: str
    ) -> dict[str, MergedChange] | None:
        """The subscription's changes in ``(since, hub cursor]``, or None if too many."""
        assert self._position is not None
        limit = self._settings.live_max_changes_per_batch
        merged: dict[str, MergedChange] = {}
        cursor = since
        for _ in range(_CATCH_UP_PAGES):
            page = await self._read(cursor, list(subscription.tables))
            for change in page.changes:
                if change.position > self._position:
                    return merged
                if change.table_id in subscription.tables and subscription.wants(
                    change.op, change.owner_user_id
                ):
                    merged[change.record_id] = merge_change(
                        merged.get(change.record_id), change
                    )
                    if len(merged) > limit:
                        return None
            if not page.more:
                return merged
            cursor = page.cursor
        return None

    def _index(self, subscription: LiveSubscription) -> None:
        for table_id in subscription.tables:
            self._by_table.setdefault(table_id, set()).add(subscription)
        self._idle_since = None

    def _unindex(self, subscription: LiveSubscription) -> None:
        for table_id in subscription.tables:
            subs = self._by_table.get(table_id)
            if subs is None:
                continue
            subs.discard(subscription)
            if not subs:
                self._by_table.pop(table_id, None)
        self._maybe_idle()

    def _maybe_idle(self) -> None:
        if not self._by_table and not self._connections and self._idle_since is None:
            self._idle_since = time.monotonic()
            self._on_idle(self)

    # -- the loop ---------------------------------------------------------------

    async def _run(self) -> None:
        try:
            self._cursor = await self._feed.horizon(self.pod_id)
            self._position = self._feed.position(self._cursor)
            self._epoch = await self._epoch_of(self.pod_id)
        except (SQLAlchemyError, OSError, LiveCursorExpired) as exc:
            self._start_error = exc
            self._started.set()
            logger.error(
                "datastore.live.hub_start_failed",
                pod_id=str(self.pod_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return
        self._next_epoch_check = (
            time.monotonic() + self._settings.live_authz_check_seconds
        )
        self._started.set()
        pump = create_background_task(
            self._pump_wakes(), name=f"live-wake-{self.pod_id}"
        )
        try:
            while True:
                woken, read_due = await self._wait_for_work()
                try:
                    if time.monotonic() >= self._next_epoch_check:
                        await self._check_epoch()
                    if read_due and self._by_table:
                        async with self._lock:
                            found = await self._drain()
                        self._plan_recheck(woken=woken, found=found)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # A failed read leaves the cursor where it was: the next
                    # wake or poll re-reads the same changes. Nothing is lost.
                    logger.error(
                        "datastore.live.hub_drain_failed",
                        pod_id=str(self.pod_id),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    await asyncio.sleep(1.0)
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump

    async def _wait_for_work(self) -> tuple[bool, bool]:
        """Sleep until there is a reason to read: (woken, a read is due).

        A read is due on a wake, on a scheduled re-check, and on the idle poll
        that covers a lost wake. An authorization-epoch tick alone wakes the
        loop but reads nothing.
        """
        now = time.monotonic()
        read_due_at = (
            self._recheck_at if self._recheck_at is not None else self._next_poll
        )
        timeout = max(0.0, min(read_due_at, self._next_epoch_check) - now)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        # Reads are at least one coalescing window apart, whatever asked for
        # them: wakes that land in the window ride one read, and a busy pod
        # costs at most one log read per window per process.
        gap = self._last_read + self._settings.live_wake_coalesce_ms / 1000
        if gap > time.monotonic():
            await asyncio.sleep(gap - time.monotonic())
        woken = self._wake.is_set()
        self._wake.clear()
        now = time.monotonic()
        if woken:
            self.stats.wakes += 1
        if woken or now >= read_due_at:
            self._next_poll = now + self._settings.live_idle_poll_seconds
            return woken, True
        return False, False

    def _plan_recheck(self, *, woken: bool, found: bool) -> None:
        """Re-read soon after a wake whose read came back empty.

        Empty after a wake means either the change was already read (its wake
        arrived while an earlier read was running) or an older transaction is
        holding the readable horizon back. Only the second needs another read,
        and nothing says which, so re-check with a doubling delay: cheap for
        the first case, prompt enough for the second.
        """
        if found:
            self._recheck_at, self._recheck_step = None, _RECHECK_FIRST
            return
        if self._recheck_step > _RECHECK_LAST or (
            not woken and self._recheck_at is None
        ):
            # Given up until something is found again; the idle poll still runs.
            self._recheck_at = None
            return
        self._recheck_at = time.monotonic() + self._recheck_step
        self._recheck_step *= 2

    async def _pump_wakes(self) -> None:
        """Turn Pub/Sub messages on the pod's channel into wakes."""
        backoff = 0.25
        while True:
            try:
                async with self._channel.subscribe(
                    [wake_channel(self.pod_id)]
                ) as messages:
                    backoff = 0.25
                    # Anything published while we were not subscribed is
                    # caught by this read.
                    self._wake.set()
                    async for _message in messages:
                        self._wake.set()
            except asyncio.CancelledError:
                raise
            except RealtimeSlowConsumerError:
                self._wake.set()
            # RuntimeError: the process-wide Pub/Sub service stopped under us.
            except (RedisError, OSError, RuntimeError) as exc:
                logger.warning(
                    "datastore.live.hub_wake_lost",
                    pod_id=str(self.pod_id),
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    async def _move_to_horizon(self) -> None:
        self._cursor = await self._feed.horizon(self.pod_id)
        self._position = self._feed.position(self._cursor)

    async def _read(self, cursor: str, table_ids: Sequence[UUID]) -> LivePage:
        self._last_read = time.monotonic()
        self.stats.reads += 1
        hub_reads.add(1)
        return await self._feed.read(
            self.pod_id, cursor, table_ids=table_ids, limit=_PAGE
        )

    async def _drain(self) -> bool:
        """Read everything readable after the hub cursor and fan it out.

        Says whether it read any change. Each read clears the pending wake
        first: a wake that arrived before the read began announced a commit
        the read can see, so it needs no read of its own.
        """
        assert self._cursor is not None
        limit = self._settings.live_max_changes_per_batch
        pending: dict[LiveSubscription, dict[str, MergedChange]] = {}
        too_many: set[LiveSubscription] = set()
        pages = 0
        found = False
        while True:
            self._wake.clear()
            try:
                page = await self._read(self._cursor, list(self._by_table))
            except LiveCursorExpired:
                await self._restart_at_horizon()
                return True
            for change in page.changes:
                route_change(
                    self._by_table.get(change.table_id, ()),
                    change,
                    pending,
                    too_many,
                    limit,
                )
            found = found or bool(page.changes)
            self._cursor = page.cursor
            self._position = self._feed.position(page.cursor)
            pages += 1
            if not page.more or pages >= _FLUSH_PAGES:
                await self._flush(pending, too_many)
                pending, too_many, pages = {}, set(), 0
            if not page.more:
                return found

    async def _flush(
        self,
        pending: dict[LiveSubscription, dict[str, MergedChange]],
        too_many: set[LiveSubscription],
    ) -> None:
        assert self._cursor is not None and self._position is not None
        self.stats.batches += 1
        cursor, position = self._cursor, self._position
        for subscription in too_many:
            subscription.advance(position, cursor)
            self._resync(subscription, cursor, ResyncReason.TOO_MANY)
        if not pending:
            return
        encoded = await encode_batch(self._feed, self.pod_id, pending, self.stats)
        for subscription, changes in encoded.items():
            previous = subscription.cursor
            subscription.advance(position, cursor)
            subscription.connection.offer_changes(
                subscription, cursor, changes, previous_cursor=previous
            )

    def _resync(
        self, subscription: LiveSubscription, cursor: str, reason: ResyncReason
    ) -> None:
        hub_resyncs.add(1, {"reason": reason.value})
        subscription.connection.offer_resync(subscription, cursor, reason)

    async def _restart_at_horizon(self) -> None:
        """The hub itself fell out of the log's window: everyone refetches."""
        await self._move_to_horizon()
        assert self._cursor is not None and self._position is not None
        for subs in self._by_table.values():
            for subscription in subs:
                subscription.advance(self._position, self._cursor)
                self._resync(subscription, self._cursor, ResyncReason.CURSOR_EXPIRED)

    # -- authorization epoch --------------------------------------------------

    async def _check_epoch(self) -> None:
        self._next_epoch_check = (
            time.monotonic() + self._settings.live_authz_check_seconds
        )
        epoch = await self._epoch_of(self.pod_id)
        if epoch is None or epoch == self._epoch:
            return
        self._epoch = epoch
        await self.recheck_access()

    async def recheck_access(self) -> None:
        """Re-check every subscription; end the ones that lost their table."""
        subscriptions = {
            subscription for subs in self._by_table.values() for subscription in subs
        }
        by_user: dict[UUID, list[LiveSubscription]] = {}
        for subscription in subscriptions:
            by_user.setdefault(subscription.user_id, []).append(subscription)
        for user_id, subs in by_user.items():
            await self._recheck_user(user_id, subs)

    async def _recheck_user(self, user_id: UUID, subs: list[LiveSubscription]) -> None:
        table_ids = sorted({table_id for sub in subs for table_id in sub.tables})
        try:
            verdicts = await self._access.still_readable(
                user_id, self.pod_id, table_ids
            )
        except LiveAccessDenied:
            verdicts = dict.fromkeys(table_ids, UnsubscribedReason.ACCESS_REVOKED.value)
        refreshed: list[LiveTable] | None = None
        async with self._lock:
            assert self._cursor is not None
            for subscription in subs:
                if not subscription.active:
                    continue
                lost = {
                    t: verdicts.get(t) for t in subscription.tables if verdicts.get(t)
                }
                if subscription.all_tables:
                    if refreshed is None:
                        try:
                            refreshed = await self._access.readable_tables(
                                user_id, self.pod_id
                            )
                        except LiveAccessDenied:
                            refreshed = []
                    current = {table.id: table.name for table in refreshed}
                    if not current:
                        self._end(subscription, UnsubscribedReason.ACCESS_REVOKED.value)
                    elif current != subscription.tables:
                        self._unindex(subscription)
                        subscription.tables = current
                        self._index(subscription)
                        self._resync(
                            subscription, self._cursor, ResyncReason.PERMISSION_CHANGED
                        )
                    continue
                if lost:
                    self._end(subscription, next(iter(lost.values())) or "")

    def _end(self, subscription: LiveSubscription, reason: str) -> None:
        self._unindex(subscription)
        subscription.connection.offer_unsubscribed(subscription, reason)
