"""One chat has one conversation link, even when a message is racing itself.

A direct chat has no channel and an email or Telegram chat may have no user id,
and a plain unique index never compares NULLs equal -- so for exactly those
chats nothing stopped two rapid first messages each inserting a link, after
which `get_by_external_thread` (which expects at most one row) failed on every
later message. `create` serialises on the chat and answers with the row that
won, so no schema change is needed.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.agent_surfaces.domain.entities import AgentSurfaceConversationLink
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
    AgentSurfaceConversationLinkModel,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_a_second_link_for_a_chat_with_no_channel_or_user_is_not_created(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    thread_id = uuid4().hex
    async with sessions() as session:
        surface = AgentSurface(
            pod_id=UUID(test_pod["id"]),
            organization_id=UUID(test_pod["organization_id"]),
            agent_id=UUID(test_pod["id"]),
            name=f"telegram-{uuid4().hex[:6]}",
            surface_type="TELEGRAM",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        session.add(surface)
        await session.flush()
        conversations = [
            ConversationModel(
                user_id=fixed_test_user["id"], pod_id=UUID(test_pod["id"])
            )
            for _ in range(2)
        ]
        session.add_all(conversations)
        await session.flush()
        surface_id = surface.id
        conversation_ids = [conversation.id for conversation in conversations]
        await session.commit()

    def link_for(conversation_id: UUID) -> AgentSurfaceConversationLink:
        return AgentSurfaceConversationLink(
            surface_id=surface_id,
            conversation_id=conversation_id,
            platform="TELEGRAM",
            external_channel_id=None,
            external_thread_id=thread_id,
            external_user_id=None,
            last_event={},
            last_inbound_at=datetime.now(timezone.utc),
        )

    async with sessions() as session:
        repository = SurfaceConversationLinkRepository(SqlAlchemyUnitOfWork(session))
        first = await repository.create(link_for(conversation_ids[0]))
        second = await repository.create(link_for(conversation_ids[1]))
        await session.commit()

    assert second.id == first.id
    assert second.conversation_id == conversation_ids[0], (
        "the loser of the race must be handed the link that stands"
    )
    async with sessions() as session:
        count = await session.scalar(
            select(func.count()).where(
                AgentSurfaceConversationLinkModel.surface_id == surface_id,
                AgentSurfaceConversationLinkModel.external_thread_id == thread_id,
            )
        )
        assert count == 1
        # And the lookup that used to raise on a duplicate answers.
        found = await SurfaceConversationLinkRepository(
            SqlAlchemyUnitOfWork(session)
        ).get_by_external_thread(
            surface_id=surface_id,
            platform="TELEGRAM",
            external_channel_id=None,
            external_thread_id=thread_id,
            external_user_id=None,
        )
        assert found is not None and found.id == first.id
