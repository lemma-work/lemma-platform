"""A group's channel timeline, read and written the way the product does.

What is said in a group is its channel's timeline. These e2e helpers seed lines
into it and read them back through the channels contracts -- the same calls the
group log, the page and a run's background make -- so a test states what the
group heard rather than which table holds it.

Not a test module; pytest does not collect it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.services.group_log import group_source
from app.modules.channels.contracts import (
    ChannelMessage,
    NewLine,
    Principal,
    Reader,
    Sender,
    background,
    post,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact


async def group_by_id(db_session: AsyncSession, group_id: UUID | str) -> SurfaceGroup:
    group = await SurfaceGroupRepository(db_session).get_by_id(UUID(str(group_id)))
    assert group is not None, "the group was never made"
    return group


async def say(
    db_session: AsyncSession,
    group: SurfaceGroup,
    text: str,
    *,
    sender: Sender,
    external_message_id: str | None = None,
) -> ChannelMessage:
    """``sender`` said ``text`` in the group, heard where the bot hears it --
    as the platform's message ``external_message_id``, where it matters."""
    uow = SqlAlchemyUnitOfWork(db_session)
    source = await group_source(uow, group)
    said = await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=sender,
            text=text,
            source_id=source.id,
            external_message_id=external_message_id or uuid4().hex,
        ),
    )
    assert said is not None
    return said


def bot_for(principal: Principal, *, agent_id: UUID) -> Sender:
    """The pod's agent, answering ``principal``."""
    return Sender.agent(agent_id, on_behalf_of=principal)


async def a_stranger(
    db_session: AsyncSession, group: SurfaceGroup, *, telegram_id: str, name: str
) -> Principal:
    """Somebody outside the pod, as a contact of its own -- the way the log
    makes one the first time they speak."""
    contact = await open_contact(
        SqlAlchemyUnitOfWork(db_session),
        pod_id=group.pod_id,
        kind=IdentityKind.TELEGRAM,
        value=telegram_id,
        strength=IdentityStrength.CHANNEL,
        display_name=name,
    )
    return Principal.contact(contact.id)


async def timeline(
    db_session: AsyncSession, group: SurfaceGroup, *, limit: int = 50
) -> list[ChannelMessage]:
    """Everything said in the group, oldest first, as a member reads it."""
    uow = SqlAlchemyUnitOfWork(db_session)
    source = await group_source(uow, group)
    return await background(
        uow, channel_id=source.channel_id, reader=Reader.member(), limit=limit
    )
