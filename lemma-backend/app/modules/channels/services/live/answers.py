"""The answers being written in a pod's channels now, as one hub knows them.

An agent answering a line says so with an ``answering`` signal
(``contracts/live.announce_answering``): ``started``, with the stream its
deltas are written on, and ``finished``. Nothing about it is stored, so the hub
remembers each answer in flight from its signal -- until it finishes, or
``LIVE_ANSWER_STREAM_SECONDS`` passes -- and that is what lets a subscriber
who joins mid-answer be told "Lem is answering…" and, if it is theirs, be
sent the answer so far (``relay.AnswerRelays.snapshot``) before its deltas.
"""

from __future__ import annotations

import time
from uuid import UUID

from pydantic import JsonValue

from app.core.live.signals import Signal
from app.core.live.topics import LiveRegistry
from app.modules.channels.services.live.events import answering_event
from app.modules.channels.services.live.relay import Answering, AnswerRelays, Deliver

#: An answer's identity: its channel, the line it answers and the agent.
type AnswerId = tuple[UUID, UUID, UUID]


def as_uuid(value: JsonValue) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def answering_of(channel_id: UUID, signal: Signal) -> Answering | None:
    """An ``answering`` signal's answer; ``None`` for one that names nobody."""
    message_id, agent_id = as_uuid(signal.get("m")), as_uuid(signal.get("a"))
    for_user = as_uuid(signal.get("for"))
    if message_id is None or agent_id is None or for_user is None:
        return None
    stream = signal.get("stream")
    return Answering(
        channel_id=channel_id,
        message_id=message_id,
        agent_id=agent_id,
        for_user_id=for_user,
        agent_run_id=str(signal["run"]) if signal.get("run") else None,
        source=str(stream.get("source")) if isinstance(stream, dict) else None,
        key=str(stream.get("key")) if isinstance(stream, dict) else None,
        conversation_id=str(signal["conv"]) if signal.get("conv") else None,
    )


def answering_for(state: str, answering: Answering, reader_id: UUID) -> str:
    """``answering.started`` / ``.finished`` as ``reader_id`` is told it."""
    own = reader_id == answering.for_user_id
    return answering_event(
        state,
        channel_id=str(answering.channel_id),
        message_id=str(answering.message_id),
        agent_id=str(answering.agent_id),
        # The run is theirs to open; to anybody else the answer is only
        # being given.
        conversation_id=answering.conversation_id if own else None,
        agent_run_id=answering.agent_run_id if own else None,
    )


class Answers:
    """The answers in flight in one pod, and those this hub is relaying."""

    def __init__(
        self, *, deliver: Deliver, streams: LiveRegistry, longest: float
    ) -> None:
        self._streams = streams
        self._longest = longest
        self.relays = AnswerRelays(deliver=deliver, longest=longest)
        #: memory: one per answer being written in the pod now, each until it
        #: finishes or ``longest`` passes.
        self._live: dict[AnswerId, tuple[Answering, float]] = {}

    def started(self, answering: Answering) -> None:
        self._live[answering.id] = (answering, time.monotonic())

    def finished(self, answering: Answering) -> None:
        self._live.pop(answering.id, None)
        self.relays.stop(answering.id)

    def now(self) -> list[Answering]:
        """The answers being written now; those past ``longest`` are forgotten."""
        horizon = time.monotonic() - self._longest
        for answer_id, (_, since) in list(self._live.items()):
            if since < horizon:
                self._live.pop(answer_id, None)
        return [answering for answering, _ in self._live.values()]

    def follow(self, answering: Answering) -> None:
        """Relay (and keep the text of) this answer, if it is not already."""
        if self.relays.following(answering.id) or not answering.source:
            return
        opener = self._streams.stream(answering.source)
        if opener is not None:
            self.relays.follow(answering, opener)

    def follow_for(self, user_id: UUID) -> None:
        """Follow every answer being written for ``user_id``."""
        for answering in self.now():
            if answering.for_user_id == user_id:
                self.follow(answering)

    def so_far(self, channel_id: UUID, reader_id: UUID) -> list[str]:
        """What a subscriber joining ``channel_id`` mid-answer is sent: that
        it is being answered, and -- to the member it answers -- the answer
        so far. Following starts here, so its deltas come next."""
        events: list[str] = []
        for answering in self.now():
            if answering.channel_id != channel_id:
                continue
            events.append(answering_for("started", answering, reader_id))
            if answering.for_user_id == reader_id:
                self.follow(answering)
                snapshot = self.relays.snapshot(answering)
                if snapshot is not None:
                    events.append(snapshot)
        return events

    async def close(self) -> None:
        self._live = {}
        await self.relays.close()


__all__ = ["AnswerId", "Answers", "answering_for", "answering_of", "as_uuid"]
