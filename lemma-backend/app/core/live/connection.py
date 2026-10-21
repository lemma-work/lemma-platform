"""One live socket: its subscriptions and its bounded send queue.

A hub never waits on a socket. It hands frames to ``offer_*``, which only
appends to an in-memory queue; a sender task per socket drains it. When a
socket's queue passes its byte budget the queued batch frames are dropped and
each subscription they belonged to gets one ``resync`` instead, so a slow
browser costs itself a refetch and costs the hub nothing.

The connection knows its subscriptions only by id and by what the topic that
made them lets it ask: whether one is still active, and how to let it go.
Everything else about a subscription is its topic's.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import JsonValue

from app.core.live.protocol import (
    CLOSE_FORBIDDEN,
    CLOSE_NOT_FOUND,
    LiveErrorCode,
    UnsubscribedReason,
    ResyncReason,
    error_frame,
    events_frame,
    result_frame,
    resync_frame,
    subscribed_frame,
    unsubscribed_frame,
)


class LiveSubscription(Protocol):
    """What a topic's subscription shows the socket that holds it."""

    @property
    def id(self) -> str: ...

    @property
    def active(self) -> bool: ...

    def release(self) -> None:
        """Stop feeding this subscription: unsubscribed, or its socket closed.
        Idempotent; never blocks."""


class FrameEncoder(Protocol):
    """How a socket words its control frames. The live socket's own words are
    ``LiveFrames``; a socket kept for an older client may word them as it did,
    or say nothing (``None``)."""

    def subscribed(self, subscription_id: str, cursor: str | None) -> str | None: ...

    def resync(
        self, subscription_id: str, cursor: str | None, reason: ResyncReason
    ) -> str | None: ...

    def unsubscribed(self, subscription_id: str, reason: str) -> str | None: ...

    def error(
        self, request_id: str | None, code: LiveErrorCode, message: str
    ) -> str | None: ...


class LiveFrames:
    def subscribed(self, subscription_id: str, cursor: str | None) -> str | None:
        return subscribed_frame(subscription_id, cursor)

    def resync(
        self, subscription_id: str, cursor: str | None, reason: ResyncReason
    ) -> str | None:
        return resync_frame(subscription_id, cursor, reason)

    def unsubscribed(self, subscription_id: str, reason: str) -> str | None:
        return unsubscribed_frame(subscription_id, reason)

    def error(
        self, request_id: str | None, code: LiveErrorCode, message: str
    ) -> str | None:
        return error_frame(request_id, code, message)


#: Largest batch frame, in encoded bytes; a larger batch is split. Kept well
#: under the 1 MiB default message limit of common websocket clients.
FRAME_BYTES = 256 * 1024


def chunk(parts: list[str], limit: int = FRAME_BYTES) -> list[list[str]]:
    """``parts`` in runs of at most ``limit`` bytes (one part may exceed it)."""
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
    #: The subscription a batch frame belongs to; ``None`` for frames that
    #: must never be dropped (control frames, resyncs, call results).
    droppable_for: str | None
    #: The subscription a queued ``resync`` is for, so a newer one replaces it.
    resync_for: str | None = None


SendText = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class _CallWindow:
    """When the last calls of one method were made, newest last."""

    at: deque[float] = field(default_factory=deque)


@dataclass(eq=False)
class LiveConnection:
    pod_id: UUID
    user_id: UUID
    send_text: SendText
    queue_bytes: int
    frames: FrameEncoder = field(default_factory=LiveFrames)
    id: str = field(default_factory=lambda: uuid4().hex)
    subscriptions: dict[str, LiveSubscription] = field(default_factory=dict)
    closed: bool = False
    overflows: int = 0
    #: The socket carries one subscription and nothing else (the previous
    #: ``/datastore/changes`` socket): the server ending it closes the socket,
    #: since its client has no frame that says so.
    ends_with_subscription: bool = False
    #: Why the server closed the socket, for its close frame; ``None`` for a
    #: plain close.
    close_code: int | None = None
    close_reason: str = ""
    _queue: deque[_Queued] = field(default_factory=deque)
    _queued_bytes: int = 0
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _calls: dict[str, _CallWindow] = field(default_factory=dict)
    #: Each subscription's newest offered batch cursor: what a resync for it
    #: carries when another subscription's batch overflows the queue.
    _offered: dict[str, str | None] = field(default_factory=dict)

    @property
    def queued_bytes(self) -> int:
        return self._queued_bytes

    # -- offers (never block) ---------------------------------------------

    def offer(self, text: str | None) -> None:
        """Queue a frame that is never dropped: control frames and errors."""
        if text is not None:
            self._push(_Queued(text, None))

    def offer_error(
        self, request_id: str | None, code: LiveErrorCode, message: str
    ) -> None:
        self.offer(self.frames.error(request_id, code, message))

    def offer_subscribed(self, subscription_id: str, cursor: str | None) -> None:
        self.offer(self.frames.subscribed(subscription_id, cursor))

    def offer_result(self, call_id: str, result: JsonValue) -> None:
        self.offer(result_frame(call_id, result))

    def offer_batch(
        self, subscription_id: str, cursor: str | None, texts: list[str]
    ) -> None:
        """Queue one batch of frames for a subscription, already encoded.

        An empty queue takes one batch of any size: a batch is already bounded
        by its topic's per-batch cap, and refusing it would turn every large
        row into a resync loop. Past the byte budget, the queued batches give
        way to one resync each.
        """
        subscription = self.subscriptions.get(subscription_id)
        if self.closed or subscription is None or not subscription.active:
            return
        if not texts:
            return
        size = sum(len(text) for text in texts)
        if self._queued_bytes and self._queued_bytes + size > self.queue_bytes:
            self._overflow(subscription_id, cursor)
            return
        for text in texts:
            self._push(_Queued(text, subscription_id))
        self._offered[subscription_id] = cursor

    def offer_events(
        self,
        subscription_id: str,
        cursor: str | None,
        events: list[str],
        *,
        previous_cursor: str | None = None,
    ) -> None:
        """Queue a topic's encoded events as ``events`` frames.

        A batch too large for one frame is split, and only its last frame
        carries the batch's cursor; the frames before it carry
        ``previous_cursor``. A client that drops mid-batch resumes from before
        the batch and receives it again, rather than from after it with the
        rest missing.
        """
        if not events:
            return
        runs = chunk(events)
        self.offer_batch(
            subscription_id,
            cursor,
            [
                events_frame(
                    subscription_id,
                    cursor if index == len(runs) - 1 else previous_cursor,
                    run,
                )
                for index, run in enumerate(runs)
            ],
        )

    def offer_resync(
        self, subscription_id: str, cursor: str | None, reason: ResyncReason
    ) -> None:
        subscription = self.subscriptions.get(subscription_id)
        if self.closed or subscription is None or not subscription.active:
            return
        self._offered[subscription_id] = cursor
        # A queued resync for the same subscription is superseded by this one:
        # one refetch covers both.
        self._drop_where(
            lambda item: subscription_id in (item.resync_for, item.droppable_for)
        )
        text = self.frames.resync(subscription_id, cursor, reason)
        if text is not None:
            self._push(_Queued(text, None, resync_for=subscription_id))

    def offer_unsubscribed(self, subscription_id: str, reason: str) -> None:
        """End a subscription from the server's side, and say why."""
        subscription = self.subscriptions.pop(subscription_id, None)
        self._offered.pop(subscription_id, None)
        if subscription is not None:
            subscription.release()
        self._drop_where(lambda item: item.droppable_for == subscription_id)
        self.offer(self.frames.unsubscribed(subscription_id, reason))
        if (
            self.ends_with_subscription
            and subscription is not None
            and reason != UnsubscribedReason.CLIENT.value
        ):
            self.close_with(
                CLOSE_FORBIDDEN
                if reason == UnsubscribedReason.ACCESS_REVOKED.value
                else CLOSE_NOT_FOUND,
                reason,
            )

    # -- calls ---------------------------------------------------------------

    def admit_call(self, method: str, per_minute: int) -> bool:
        """Whether another ``method`` call fits this socket's minute."""
        window = self._calls.setdefault(method, _CallWindow())
        now = time.monotonic()
        while window.at and now - window.at[0] >= 60.0:
            window.at.popleft()
        if len(window.at) >= per_minute:
            return False
        window.at.append(now)
        return True

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

    def close_with(self, code: int, reason: str) -> None:
        """Close the socket from the server's side with ``code``: the sender
        stops, and the socket's runner closes it with that code."""
        if self.closed:
            return
        self.close_code, self.close_reason = code, reason
        self.close()

    def close(self) -> None:
        """Stop sending and let every subscription go."""
        self.closed = True
        subscriptions, self.subscriptions = list(self.subscriptions.values()), {}
        self._offered.clear()
        for subscription in subscriptions:
            subscription.release()
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

    def _overflow(self, subscription_id: str, cursor: str | None) -> None:
        """Replace every queued batch frame with one resync per subscription.

        Each resync carries that subscription's newest cursor -- ``cursor`` for
        the batch that did not fit, the last one offered for the others --
        which is at or after everything of it that was dropped: the client
        refetches after receiving it. Subscriptions of different topics count
        in different cursors, so one cursor for all would not do.
        """
        self.overflows += 1
        affected = self._drop_where(lambda item: item.droppable_for is not None)
        affected.add(subscription_id)
        for affected_id in sorted(affected):
            self.offer_resync(
                affected_id,
                cursor
                if affected_id == subscription_id
                else self._offered.get(affected_id),
                ResyncReason.OVERFLOW,
            )


__all__ = [
    "FRAME_BYTES",
    "FrameEncoder",
    "LiveConnection",
    "LiveFrames",
    "LiveSubscription",
    "SendText",
    "chunk",
]
