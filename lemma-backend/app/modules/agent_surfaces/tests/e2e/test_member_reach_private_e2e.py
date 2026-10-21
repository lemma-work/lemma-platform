"""A message meant for one person goes somewhere private, never to a group.

Reaching a member proactively writes to their chat on the surface, because a
bot cannot open one cold. A member who last mentioned the bot in a Telegram
group must not have their private notifications posted into that group, in
front of everyone in it -- so the lookup is of the person's private places
only, and a member with none is unreachable here (the notification then falls
back to email or their inbox).
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.channels.contracts import (
    AccountKey,
    GroupHome,
    MembersHome,
    Place,
    SourceKind,
    source_for,
)
from app.modules.channels.contracts.places import person_places
from app.modules.channels.infrastructure.models import ChannelSourceModel

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

MEMBER = "900100"


async def _surface(sessions, pod) -> UUID:
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
        await session.commit()
        return surface.id


async def _heard(
    sessions, pod, member: UUID, surface_id, *, private: bool, at: datetime
) -> UUID:
    """The member's private chat, or a group they spoke in, heard ``at``."""
    async with sessions() as session:
        source = await source_for(
            SqlAlchemyUnitOfWork(session),
            pod_id=UUID(pod["id"]),
            place=Place(
                surface_id=surface_id,
                platform="TELEGRAM",
                account=AccountKey(workspace=f"surface:{surface_id}"),
                kind=SourceKind.PERSON if private else SourceKind.CHAT,
                external_channel_id=None if private else f"-100{uuid4().int % 10**9}",
                external_user_id=MEMBER if private else None,
            ),
            home=MembersHome(user_id=member) if private else GroupHome(title="Ops"),
        )
        await session.execute(
            update(ChannelSourceModel)
            .where(ChannelSourceModel.id == source.id)
            .values(last_inbound_at=at)
        )
        await session.commit()
        return source.id


async def _member_places(sessions, surface_id):
    async with sessions() as session:
        return await person_places(
            SqlAlchemyUnitOfWork(session),
            platform="TELEGRAM",
            external_user_id=MEMBER,
            surface_ids=[surface_id],
        )


async def test_the_private_chat_is_found_over_a_fresher_group(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    surface_id = await _surface(sessions, test_pod)
    now = datetime.now(timezone.utc)
    member = UUID(str(fixed_test_user["id"]))
    private = await _heard(
        sessions, test_pod, member, surface_id, private=True, at=now - timedelta(days=2)
    )
    await _heard(sessions, test_pod, member, surface_id, private=False, at=now)

    places = await _member_places(sessions, surface_id)

    assert [place.id for place in places] == [private]


async def test_a_member_who_only_spoke_in_a_group_is_not_reached_there(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    surface_id = await _surface(sessions, test_pod)
    await _heard(
        sessions,
        test_pod,
        UUID(str(fixed_test_user["id"])),
        surface_id,
        private=False,
        at=datetime.now(timezone.utc),
    )

    assert await _member_places(sessions, surface_id) == []
