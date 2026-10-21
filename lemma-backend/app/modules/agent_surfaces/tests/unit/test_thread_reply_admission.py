"""Which threaded replies are ours to answer, and what asking costs.

`is_thread_reply` comes from the payload -- Teams' `replyToId`, Slack's
`thread_ts` -- so it says "this is a reply" and never "this is a reply to us".
Admitting on it alone meant every threaded reply by a pod member, anywhere in a
connected channel, started an agent run: two colleagues talking under somebody
else's message paid for a model call each, and the run opened with no
conversation to continue.

`admitted_surfaces` asks the channel instead: whether this sender already said
something in this thread, at the place the surface hears the chat, that a
conversation took up. These pin both halves of that: the answer, and that the
read happens only where it replaces a run.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.surface_candidates import (
    admitted_surfaces,
)
from app.modules.channels.contracts import Source, SourceKind
from app.modules.test_support.channel_seats_double import InMemoryChannelSeats

pytestmark = pytest.mark.asyncio


class _Seats(InMemoryChannelSeats):
    """The seats, with every read counted."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    async def places_hearing(self, **kwargs):
        self.reads.append("places_hearing")
        return await super().places_hearing(**kwargs)

    async def conversation_at(self, source_id, **kwargs):
        self.reads.append("conversation_at")
        return await super().conversation_at(source_id, **kwargs)


def _channel_place(surface: AgentSurfaceEntity) -> Source:
    return Source(
        id=uuid4(),
        channel_id=uuid4(),
        pod_id=surface.pod_id,
        account_id=uuid4(),
        surface_id=surface.id,
        platform="SLACK",
        kind=SourceKind.CHAT,
        external_channel_id="C1",
    )


def _surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.SLACK,
        account_id=None,
        external_workspace_id="T1",
        config=SurfaceConfig(channels=[SurfaceChannelRoute(channel_id="C1")]),
        is_active=True,
        surface_identity_id="U-BOT",
    )


def _reply(*, mentioned: bool) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        tenant_id="T1",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_channel_id="C1",
        external_thread_id="1700000000.1",
        sender_external_user_id="U-PERSON",
        message_text="…and another thing",
        is_dm=False,
        mentioned_agent=mentioned,
        metadata={
            "is_thread_reply": True,
            "event_type": "app_mention" if mentioned else "message",
        },
    )


async def test_a_reply_in_a_thread_we_are_in_is_admitted():
    surface = _surface()
    seats = _Seats()
    seats.answer(
        uuid4(),
        _channel_place(surface),
        _reply(mentioned=True).model_dump(mode="json"),
    )

    admitted = await admitted_surfaces([surface], _reply(mentioned=False), seats=seats)

    assert admitted == [surface]


async def test_somebody_elses_conversation_in_the_thread_does_not_admit_the_reply():
    """Asked on the key routing continues a conversation with -- this sender
    in this thread. The equivalence is the point: see `admitted_surfaces`."""
    surface = _surface()
    seats = _Seats()
    said_by_another = _reply(mentioned=True).model_copy(
        update={"sender_external_user_id": "U-SOMEBODY-ELSE"}
    )
    seats.answer(
        uuid4(), _channel_place(surface), said_by_another.model_dump(mode="json")
    )

    admitted = await admitted_surfaces([surface], _reply(mentioned=False), seats=seats)

    assert admitted == []


async def test_a_reply_in_a_thread_we_are_not_in_is_dropped():
    """The case this exists for, and the one that used to start a run."""
    seats = _Seats()

    admitted = await admitted_surfaces(
        [_surface()], _reply(mentioned=False), seats=seats
    )

    assert admitted == []
    assert seats.reads == ["places_hearing"]


async def test_a_mentioned_reply_is_admitted_without_reading_anything():
    """The @mention is the universal trigger, and it costs no query.

    Worth pinning: the read is on the leftover path only. If it ever moved in
    front of the mention check it would land on every channel message there is.
    """
    surface = _surface()
    seats = _Seats()

    admitted = await admitted_surfaces([surface], _reply(mentioned=True), seats=seats)

    assert admitted == [surface]
    assert seats.reads == []


async def test_a_message_that_is_not_a_thread_reply_reads_nothing():
    surface = _surface()
    seats = _Seats()
    event = _reply(mentioned=True)
    event.metadata["is_thread_reply"] = False

    assert await admitted_surfaces([surface], event, seats=seats) == [surface]
    assert seats.reads == []
