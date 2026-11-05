"""One person, one agent, one channel: one conversation, even under a race.

Two messages from one person arriving together each place the message, then
look for the person's conversation in the channel. Without the lock on the
person both find nothing and both open one, and the person gets two answers
from two contexts. The lock serialises them, and the unique index on the
conversation's channel, agent and person is the floor under it.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.agent_surfaces.composition import build_conversation_binder
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_agent_id
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.channels.infrastructure.models import (
    ChannelSourceModel,
    ExternalAccountModel,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _telegram(sessions, test_pod):
    pod_id = UUID(test_pod["id"])
    async with sessions() as session:
        row = AgentSurface(
            pod_id=pod_id,
            organization_id=UUID(test_pod["organization_id"]),
            agent_id=pod_id,
            name=f"telegram-{uuid4().hex[:6]}",
            surface_type="TELEGRAM",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        session.add(row)
        await session.flush()
        surface = row.to_entity()
        agent_name = await agent_name_for_agent_id(
            SqlAlchemyUnitOfWork(session), pod_id
        )
        await session.commit()
    route = ResolvedSurfaceRoute(
        pod_id=pod_id,
        agent_id=pod_id,
        agent_name=agent_name,
        agent_display_name="Assistant",
        conversation_kind="DM",
        route_key="dm",
    )
    return surface, route


def _binding(sessions, surface, route, user_id: UUID, person: str):
    async def one_message(text: str, *, thread: str | None = None) -> UUID:
        chat = thread or person
        async with sessions() as session:
            bound = await build_conversation_binder(
                SqlAlchemyUnitOfWork(session)
            ).bind_conversation(
                surface=surface,
                parsed=ParsedInboundSurfaceEvent(
                    platform="TELEGRAM",
                    conversation_type=ConversationType.EXTERNAL_DM,
                    external_channel_id=chat,
                    external_thread_id=chat,
                    external_message_id=uuid4().hex,
                    sender_external_user_id=person,
                    message_text=text,
                    is_dm=True,
                ),
                resolved_user=ResolvedSurfaceUser(
                    internal_user_id=user_id, external_user_id=person
                ),
                route=route,
            )
            await session.commit()
            return bound.conversation_id

    return one_message


async def _count(sessions, *where) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).where(*where)) or 0


async def test_two_first_messages_at_once_open_one_conversation(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    surface, route = await _telegram(sessions, test_pod)
    person = f"tg-{uuid4().hex[:8]}"
    one_message = _binding(
        sessions, surface, route, UUID(fixed_test_user["id"]), person
    )

    first, second = await asyncio.gather(
        one_message("are you there?"), one_message("hello?")
    )

    assert first == second
    assert (
        await _count(
            sessions,
            # A place is in the account the surface reaches it through.
            ChannelSourceModel.account_id.in_(
                select(ExternalAccountModel.id).where(
                    ExternalAccountModel.surface_id == surface.id
                )
            ),
            ChannelSourceModel.external_user_id == person,
        )
        == 1
    ), "the person's chat is one place"


async def test_a_burst_after_a_quiet_day_continues_the_one_conversation(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    """A quiet day, a new thread, then two messages at once: all one conversation.

    A person has one conversation with the agent in their direct channel,
    however long they were away and whatever thread they write in.
    """
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    surface, route = await _telegram(sessions, test_pod)
    user_id = UUID(fixed_test_user["id"])
    person = f"tg-{uuid4().hex[:8]}"
    one_message = _binding(sessions, surface, route, user_id, person)
    yesterdays = await one_message("hello")
    async with sessions() as session:
        await session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == yesterdays)
            .values(last_activity_at=datetime.now(timezone.utc) - timedelta(hours=25))
        )
        await session.commit()

    first, second = await asyncio.gather(
        one_message("are you there?", thread=uuid4().hex),
        one_message("hello again", thread=uuid4().hex),
    )

    assert first == second == yesterdays
    assert (
        await _count(
            sessions,
            ConversationModel.pod_id == UUID(test_pod["id"]),
            ConversationModel.user_id == user_id,
            ConversationModel.channel_id.is_not(None),
            ConversationModel.created_at
            >= datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        >= 1
    )
