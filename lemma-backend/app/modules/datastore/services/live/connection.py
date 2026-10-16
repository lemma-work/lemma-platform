"""One live socket: its subscriptions and its bounded send queue.

The hub never waits on a socket. It hands frames to ``offer_*``, which only
appends to an in-memory queue; a sender task per socket drains it. When a
socket's queue passes its byte budget the queued change frames are dropped and
each subscription they belonged to gets one ``resync`` instead, so a slow
browser costs itself a refetch and costs the hub nothing.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.modules.datastore.domain.live import (
    ChangePosition,
    EncodedChange,
    LiveConditions,
    LiveErrorCode,
    LiveOp,
    ResyncReason,
    changes_frame,
    error_frame,
    legacy_ready_frame,
    resync_frame,
    subscribed_frame,
    unsubscribed_frame,
)


@dataclass(eq=False, slots=True)
class LiveSubscription:
    id: str
    connection: "LiveConnection"
    #: Table id to name, for every table this subscription covers.
    tables: dict[UUID, str]
    #: Subscribed without a table: follows the caller's readable set.
    all_tables: bool
    ops: frozenset[LiveOp] | None
    #: The log position this subscription has been brought up to; the hub
    #: delivers only changes after it.
    position: ChangePosition
    #: ``position`` as the client's cursor: what a resume from here sends.
    cursor: str = ""
    active: bool = True
    #: ``when`` over the current row; applied once the row is fetched.
    when: LiveConditions | None = None

    def advance(self, position: ChangePosition, cursor: str) -> None:
        self.position = position
        self.cursor = cursor

    @property
    def user_id(self) -> UUID:
        return self.connection.user_id

    def wants(self, op: LiveOp, owner_user_id: UUID | None) -> bool:
        if self.ops is not None and op not in self.ops:
            return False
        # RLS: a row with an owner reaches only that owner; a shared row
        # (no owner) reaches every reader of the table.
        return owner_user_id is None or owner_user_id == self.connection.user_id


#: Largest ``changes`` frame, in encoded bytes; a larger batch is split. Kept
#: well under the 1 MiB default message limit of common websocket clients.
FRAME_BYTES = 256 * 1024


def _chunk(parts: list[str], limit: int) -> list[list[str]]:
    chunks: list[list[str]] = [[]]
    size = 0
    for part in parts:
        if chunks[-1] and size + len(part) > limit:
            chunks.append([])
            size = 0
        chunks[-1].append(part)
        size += len(part)
    return chunks


@dataclass(slots=True)
class _Queued:
    text: str
    #: The subscription a ``changes`` frame belongs to; ``None`` for frames
    #: that must never be dropped (control frames, resyncs).
    droppable_for: str | None
    #: The subscription a queued ``resync`` is for, so a newer one replaces it.
    resync_for: str | None = None


SendText = Callable[[str], Awaitable[None]]


@dataclass(eq=False)
class LiveConnection:
    pod_id: UUID
    user_id: UUID
    send_text: SendText
    queue_bytes: int
    #: The ``/datastore/changes`` shim: one frame per change, no resync frame.
    legacy: bool = False
    id: str = field(default_factory=lambda: uuid4().hex)
    subscriptions: dict[str, LiveSubscription] = field(default_factory=dict)
    closed: bool = False
    overflows: int = 0
    _queue: deque[_Queued] = field(default_factory=deque)
    _queued_bytes: int = 0
    _wake: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def queued_bytes(self) -> int:
        return self._queued_bytes

    # -- offers (never block) ---------------------------------------------

    def offer(self, text: str) -> None:
        """Queue a frame that is never dropped: control frames and errors."""
        self._push(_Queued(text, None))

    def offer_error(
        self, subscription_id: str | None, code: LiveErrorCode, message: str
    ) -> None:
        # The shim refuses before it subscribes; its clients have no error frame.
        if not self.legacy:
            self.offer(error_frame(subscription_id, code, message))

    def offer_subscribed(self, subscription: LiveSubscription, cursor: str) -> None:
        if self.legacy:
            # The previous SDK learns its resume cursor from `ready`.
            self.offer(legacy_ready_frame(cursor))
        else:
            self.offer(subscribed_frame(subscription.id, cursor))

    def offer_changes(
        self,
        subscription: LiveSubscription,
        cursor: str,
        changes: list[EncodedChange],
        *,
        previous_cursor: str,
    ) -> None:
        """Queue one batch, split into frames of at most ``FRAME_BYTES``.

        Only the batch's last frame carries the batch's cursor; the frames
        before it carry ``previous_cursor``. A client that drops mid-batch
        resumes from before the batch and receives it again, rather than from
        after it with the rest missing.
        """
        if self.closed or not subscription.active or not changes:
            return
        if self.legacy:
            texts = [
                change.legacy_frame(
                    pod_id=self.pod_id,
                    cursor=cursor if index == len(changes) - 1 else previous_cursor,
                )
                for index, change in enumerate(changes)
            ]
        else:
            chunks = _chunk([change.live_json() for change in changes], FRAME_BYTES)
            texts = [
                changes_frame(
                    subscription.id,
                    cursor if index == len(chunks) - 1 else previous_cursor,
                    chunk,
                )
                for index, chunk in enumerate(chunks)
            ]
        size = sum(len(text) for text in texts)
        # An empty queue takes one batch of any size: a batch is already
        # bounded by the per-batch change cap, and refusing it would turn every
        # large-row table into a resync loop.
        if self._queued_bytes and self._queued_bytes + size > self.queue_bytes:
            self._overflow(subscription, cursor)
            return
        for text in texts:
            self._push(_Queued(text, subscription.id))

    def offer_resync(
        self, subscription: LiveSubscription, cursor: str, reason: ResyncReason
    ) -> None:
        if self.closed or not subscription.active:
            return
        # A queued resync for the same subscription is superseded by this one:
        # one refetch covers both.
        self._drop_where(
            lambda item: subscription.id in (item.resync_for, item.droppable_for)
        )
        if self.legacy:
            # The previous SDK has no resync: it treats an unknown frame as a
            # change. A fresh `ready` moves its resume cursor past the gap.
            text = legacy_ready_frame(cursor)
        else:
            text = resync_frame(subscription.id, cursor, reason)
        self._push(_Queued(text, None, resync_for=subscription.id))

    def offer_unsubscribed(self, subscription: LiveSubscription, reason: str) -> None:
        subscription.active = False
        self.subscriptions.pop(subscription.id, None)
        self._drop_where(lambda item: item.droppable_for == subscription.id)
        if not self.legacy:
            self.offer(unsubscribed_frame(subscription.id, reason))

    # -- the sender ----------------------------------------------------------

    async def run_sender(self) -> None:
        """Drain the queue to the socket until the connection closes."""
        while not self.closed:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            item = self._queue.popleft()
            self._queued_bytes -= len(item.text)
            await self.send_text(item.text)

    def close(self) -> None:
        self.closed = True
        for subscription in self.subscriptions.values():
            subscription.active = False
        self._queue.clear()
        self._queued_bytes = 0
        self._wake.set()

    # -- internals -----------------------------------------------------------

    def _push(self, item: _Queued) -> None:
        if self.closed:
            return
        self._queue.append(item)
        self._queued_bytes += len(item.text)
        self._wake.set()

    def _drop_where(self, predicate: Callable[[_Queued], bool]) -> set[str]:
        kept: deque[_Queued] = deque()
        dropped_for: set[str] = set()
        for item in self._queue:
            if predicate(item):
                self._queued_bytes -= len(item.text)
                if item.droppable_for is not None:
                    dropped_for.add(item.droppable_for)
            else:
                kept.append(item)
        self._queue = kept
        return dropped_for

    def _overflow(self, subscription: LiveSubscription, cursor: str) -> None:
        """Replace every queued change frame with one resync per subscription.

        Every queued frame is at or before ``cursor``, so a resync at ``cursor``
        covers all of them: the client refetches after receiving it, which is
        later than anything that was dropped.
        """
        self.overflows += 1
        affected = self._drop_where(lambda item: item.droppable_for is not None)
        affected.add(subscription.id)
        for subscription_id in sorted(affected):
            target = self.subscriptions.get(subscription_id)
            if target is not None:
                self.offer_resync(target, cursor, ResyncReason.OVERFLOW)
