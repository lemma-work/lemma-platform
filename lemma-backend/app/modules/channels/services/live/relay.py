"""An agent's answer as it is written, relayed into the channel it answers.

When an agent starts answering a line, whatever runs it signals
``answering`` with a *stream* to follow -- a source name and key that core's
live registry opens (``LiveRegistry.stream``). This module never learns what
the source is; the agent module registers it. A hub with a subscriber who may
read the answer follows the stream and sends each delta as ``line.streaming``
until the run ends, the agent says it is done, or ``LIVE_ANSWER_STREAM_SECONDS``
passes. The finished answer still arrives as its line: deltas are display.

Only the reader the answer is made for is sent deltas -- the member whose line
it answers, with whose access it is written -- because the line, once it
exists, is withheld from everybody else (``domain.projection.withheld_from``).

A hub follows an answer as soon as that reader has any subscription in it --
their inbox is enough -- and keeps the text written so far (up to
``SNAPSHOT_CHARS``), so a reader who opens the channel mid-answer is sent it
whole (``line.streaming`` with ``kind: "snapshot"``) and then the deltas that
follow.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from redis.exceptions import RedisError

from app.core.live.protocol import JsonObject
from app.core.live.topics import StreamOpener
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.modules.channels.services.live.events import streaming_event

logger = get_logger(__name__)

#: A run's own events that end its stream.
_TERMINAL = frozenset({"completed", "error", "stopped"})
#: The most of an answer kept for a reader who joins mid-answer. Past it the
#: snapshot is the start of the answer; the finished line is whole anyway.
SNAPSHOT_CHARS = 32_000
#: The token kind a snapshot is sent as: the answer so far, replacing whatever
#: the client had built from deltas.
SNAPSHOT_KIND = "snapshot"


@dataclass(frozen=True, slots=True)
class Answering:
    """One agent answering one line, as its ``answering`` signal says."""

    channel_id: UUID
    message_id: UUID
    agent_id: UUID
    #: The member it answers for: the one reader shown what it writes.
    for_user_id: UUID
    agent_run_id: str | None
    source: str | None
    key: str | None
    conversation_id: str | None = None

    @property
    def id(self) -> tuple[UUID, UUID, UUID]:
        return (self.channel_id, self.message_id, self.agent_id)


#: Hands a ``line.streaming`` event to a reader's subscriptions on a channel.
type Deliver = Callable[[UUID, UUID, str], None]


class AnswerRelays:
    """The answers this hub is following, one task each."""

    def __init__(self, *, deliver: Deliver, longest: float) -> None:
        self._deliver = deliver
        self._longest = longest
        #: memory: one per answer in flight with a reader in this process.
        self._tasks: dict[tuple[UUID, UUID, UUID], asyncio.Task[None]] = {}
        #: memory: the text so far of each answer followed, at most
        #: ``SNAPSHOT_CHARS`` each; dropped with its task.
        self._text: dict[tuple[UUID, UUID, UUID], _Written] = {}

    def following(self, answer_id: tuple[UUID, UUID, UUID]) -> bool:
        task = self._tasks.get(answer_id)
        return task is not None and not task.done()

    def snapshot(self, answering: Answering) -> str | None:
        """The answer so far as a ``line.streaming`` snapshot; ``None`` when
        nothing has been written yet (or it is not followed here)."""
        written = self._text.get(answering.id)
        if written is None or not written.parts or not self.following(answering.id):
            return None
        return _streaming(answering, kind=SNAPSHOT_KIND, delta="".join(written.parts))

    def follow(self, answering: Answering, opener: StreamOpener) -> None:
        if answering.key is None or answering.agent_run_id is None:
            return
        current = self._tasks.get(answering.id)
        if current is not None and not current.done():
            return
        task = create_background_task(
            self._relay(answering, opener), name="live-answer-relay"
        )
        self._tasks[answering.id] = task
        self._text[answering.id] = _Written()
        task.add_done_callback(lambda done: self._forget(answering.id, done))

    def _forget(
        self, answer_id: tuple[UUID, UUID, UUID], done: asyncio.Task[None]
    ) -> None:
        if self._tasks.get(answer_id) is done:
            self._tasks.pop(answer_id, None)
            self._text.pop(answer_id, None)

    def stop(self, answer_id: tuple[UUID, UUID, UUID]) -> None:
        task = self._tasks.pop(answer_id, None)
        self._text.pop(answer_id, None)
        if task is not None:
            task.cancel()

    async def close(self) -> None:
        self._text = {}
        tasks, self._tasks = list(self._tasks.values()), {}
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _relay(self, answering: Answering, opener: StreamOpener) -> None:
        assert answering.key is not None
        try:
            async with asyncio.timeout(self._longest):
                async with opener(answering.key) as events:
                    async for event in events:
                        if event.get("agent_run_id") not in (
                            None,
                            answering.agent_run_id,
                        ):
                            continue
                        kind = str(event.get("type", ""))
                        if kind in _TERMINAL:
                            return
                        if kind == "token":
                            self._send(answering, event)
        except TimeoutError:
            return
        except (RedisError, OSError, RuntimeError) as exc:
            # The stream is display only: the answer still arrives as a line.
            logger.warning(
                "channels.live.answer_relay_lost.degraded",
                channel_id=str(answering.channel_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )

    def _send(self, answering: Answering, event: JsonObject) -> None:
        kind = str(event.get("kind") or "text")
        delta = str(event.get("data") or "")
        written = self._text.get(answering.id)
        if written is not None:
            written.add(kind, delta)
        self._deliver(
            answering.channel_id,
            answering.for_user_id,
            _streaming(answering, kind=kind, delta=delta),
        )


@dataclass(slots=True)
class _Written:
    """An answer's visible text so far, bounded."""

    parts: list[str] = field(default_factory=list)
    size: int = 0

    def add(self, kind: str, delta: str) -> None:
        if kind == "stream_reset":
            # The run started its answer over: so does the snapshot.
            self.parts, self.size = [], 0
            return
        if kind != "text" or self.size >= SNAPSHOT_CHARS:
            return
        kept = delta[: SNAPSHOT_CHARS - self.size]
        self.parts.append(kept)
        self.size += len(kept)


def _streaming(answering: Answering, *, kind: str, delta: str) -> str:
    return streaming_event(
        channel_id=str(answering.channel_id),
        message_id=str(answering.message_id),
        agent_id=str(answering.agent_id),
        agent_run_id=str(answering.agent_run_id),
        kind=kind,
        delta=delta,
    )


__all__ = ["SNAPSHOT_CHARS", "SNAPSHOT_KIND", "AnswerRelays", "Answering"]
