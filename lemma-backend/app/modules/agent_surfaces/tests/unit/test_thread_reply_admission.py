"""Which threaded replies are ours to answer, and what asking costs.

`is_thread_reply` comes from the payload -- Teams' `replyToId`, Slack's
`thread_ts` -- so it says "this is a reply" and never "this is a reply to us".
Admitting on it alone meant every threaded reply by a pod member, anywhere in a
connected channel, started an agent run: two colleagues talking under somebody
else's message paid for a model call each, and the run opened with no
conversation to continue.

`_admit` asks the conversation link instead, on the same key routing continues a
conversation with. These pin both halves of that: the answer, and that the read
happens only where it replaces a run.
"""

from __future__ import annotations

from uuid import UUID, uuid4

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

pytestmark = pytest.mark.asyncio


class _Links:
    """The continuity read, handed over and counted."""

    def __init__(self, answer: UUID | None = None):
        self.answer = answer
        self.calls: list[dict] = []

    async def find_surface_id_for_external_thread(self, **kwargs):
        self.calls.append(kwargs)
        return self.answer


def _surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.SLACK,
        account_id=None,
        config=SurfaceConfig(channels=[SurfaceChannelRoute(channel_id="C1")]),
        is_active=True,
        surface_identity_id="U-BOT",
    )


def _reply(*, mentioned: bool) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
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
    links = _Links(answer=surface.id)

    admitted = await admitted_surfaces([surface], _reply(mentioned=False), links=links)

    assert admitted == [surface]
    # Asked on the key routing would continue the conversation with: thread and
    # sender. The equivalence is the point -- see `admitted_surfaces`.
    assert links.calls[0]["external_thread_id"] == "1700000000.1"
    assert links.calls[0]["external_user_id"] == "U-PERSON"


async def test_a_reply_in_a_thread_we_are_not_in_is_dropped():
    """The case this exists for, and the one that used to start a run."""
    links = _Links(answer=None)

    admitted = await admitted_surfaces(
        [_surface()], _reply(mentioned=False), links=links
    )

    assert admitted == []
    assert len(links.calls) == 1


async def test_a_mentioned_reply_is_admitted_without_reading_anything():
    """The @mention is the universal trigger, and it costs no query.

    Worth pinning: the read is on the leftover path only. If it ever moved in
    front of the mention check it would land on every channel message there is.
    """
    surface = _surface()
    links = _Links(answer=None)

    admitted = await admitted_surfaces([surface], _reply(mentioned=True), links=links)

    assert admitted == [surface]
    assert links.calls == []


async def test_a_message_that_is_not_a_thread_reply_reads_nothing():
    surface = _surface()
    links = _Links(answer=None)
    event = _reply(mentioned=True)
    event.metadata["is_thread_reply"] = False

    assert await admitted_surfaces([surface], event, links=links) == [surface]
    assert links.calls == []
