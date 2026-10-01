"""A message meant for one person goes somewhere private, never to a group.

Reaching a member proactively reuses their latest thread on the surface, because
a bot cannot open a chat cold. "Latest" used to mean latest of any kind -- so a
member who had last mentioned the bot in a Telegram group had their private
notifications posted into that group, in front of everyone in it. Groups being
first-class makes that the common case rather than a corner, so the lookup is
held to private threads, and a member with none is unreachable here (the
notification then falls back to email or their inbox).
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.agent_surfaces.domain.entities import AgentSurfaceConversationLink
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

MEMBER = "900100"


async def _surface_and_conversations(sessions, *, pod, owner_id, count: int):
    async with sessions() as session:
        surface = AgentSurface(
            pod_id=UUID(pod["id"]),
            organization_id=UUID(pod["organization_id"]),
            agent_id=UUID(pod["id"]),
            name=f"telegram-{uuid4().hex[:6]}",
            surface_type="TELEGRAM",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        session.add(surface)
        await session.flush()
        conversations = [
            ConversationModel(user_id=owner_id, pod_id=UUID(pod["id"]))
            for _ in range(count)
        ]
        session.add_all(conversations)
        await session.flush()
        ids = (surface.id, [conversation.id for conversation in conversations])
        await session.commit()
    return ids


def _link(surface_id, conversation_id, *, kind: str, thread: str, at: datetime):
    return AgentSurfaceConversationLink(
        surface_id=surface_id,
        conversation_id=conversation_id,
        platform="TELEGRAM",
        external_channel_id=thread,
        external_thread_id=thread,
        external_user_id=MEMBER,
        conversation_kind=kind,
        last_event={},
        last_inbound_at=at,
    )


async def test_the_private_thread_is_chosen_over_a_fresher_group_one(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    surface_id, (private, group) = await _surface_and_conversations(
        sessions, pod=test_pod, owner_id=fixed_test_user["id"], count=2
    )
    now = datetime.now(timezone.utc)
    async with sessions() as session:
        links = SurfaceConversationLinkRepository(SqlAlchemyUnitOfWork(session))
        await links.create(
            _link(
                surface_id,
                private,
                kind="DM",
                thread=MEMBER,
                at=now - timedelta(days=2),
            )
        )
        await links.create(
            _link(surface_id, group, kind="CHANNEL", thread="-100123", at=now)
        )
        await session.commit()

    async with sessions() as session:
        found = await SurfaceConversationLinkRepository(
            SqlAlchemyUnitOfWork(session)
        ).get_latest_by_surface_and_external_user(
            surface_id=surface_id, external_user_id=MEMBER
        )

    assert found is not None
    assert found.conversation_id == private


async def test_a_member_who_only_spoke_in_a_group_is_not_reached_there(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    surface_id, (group,) = await _surface_and_conversations(
        sessions, pod=test_pod, owner_id=fixed_test_user["id"], count=1
    )
    async with sessions() as session:
        await SurfaceConversationLinkRepository(SqlAlchemyUnitOfWork(session)).create(
            _link(
                surface_id,
                group,
                kind="CHANNEL",
                thread="-100123",
                at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    async with sessions() as session:
        found = await SurfaceConversationLinkRepository(
            SqlAlchemyUnitOfWork(session)
        ).get_latest_by_surface_and_external_user(
            surface_id=surface_id, external_user_id=MEMBER
        )

    assert found is None
