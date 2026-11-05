"""A turn on a platform refused before its run started is said once, to the
member, as a note in their own channel (``services/refused_turns``).

Against Postgres: the note is the channel's line, keyed, so a group that keeps
addressing an agent it cannot run for is one note -- not one per message, and
not a push.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.contracts import RuntimeUnavailableError
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata
from app.modules.agent_surfaces.services.refused_turns import note_refused_turn
from app.modules.agent_surfaces.tests.unit.surface_doubles import _slack_event
from app.modules.channels.contracts import direct_channel
from app.modules.channels.infrastructure.models import ChannelMessageModel

pytestmark = pytest.mark.e2e


def _in_a_group(pod_id: UUID, user_id: UUID) -> SurfaceChatContext:
    event = _slack_event().model_copy(update={"is_dm": False})
    return SurfaceChatContext(
        platform="SLACK",
        pod_id=pod_id,
        surface_id=uuid4(),
        conversation_id=uuid4(),
        user_id=user_id,
        agent_name="helper",
        agent_display_name="Helper",
        message_text="@helper what's on?",
        message_metadata=SurfaceMessageMetadata(surface_platform="SLACK"),
        message_user_id=user_id,
        event=event,
        source_id=uuid4(),
    )


async def test_a_refused_turn_is_one_note_in_the_members_own_channel(scenario):
    await scenario.create_org_with_pod(name_prefix="Refused")
    pod_id, owner = UUID(scenario.pod_id), UUID(scenario.owner_user["id"])
    context = _in_a_group(pod_id, owner)
    refused = RuntimeUnavailableError(
        "This teammate runs on Ada's own Claude Code, which only they can use.",
        code="runtime_profile_not_shareable",
    )
    heard = AsyncMock()

    for _ in range(3):
        async with create_uow_from_session_maker(async_session_maker) as uow:
            await note_refused_turn(
                uow,
                context,
                message_text="@helper what's on?",
                refused=refused,
                lines=SimpleNamespace(heard=heard),
            )
            await uow.commit()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        own = await direct_channel(uow, pod_id=pod_id, user_id=owner)
    async with async_session_maker() as session:
        notes = list(
            await session.scalars(
                select(ChannelMessageModel).where(
                    ChannelMessageModel.channel_id == own.id
                )
            )
        )
    [note] = notes
    assert note.members_only
    assert "Helper didn't answer your message on Slack." in note.body
    assert "Ada's own Claude Code" in note.body
    assert heard.await_count == 3
