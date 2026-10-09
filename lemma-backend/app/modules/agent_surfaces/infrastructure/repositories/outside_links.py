"""Whether routing linked a conversation to people outside the pod.

Its own module because it is asked from both sides of the boundary -- the
agent's run deciding whose authority it has, and a question being passed on --
and because it must recognise every kind of outside link: a group's strangers'
thread, each contact's private chat, and a web widget visitor's chat.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.groups import (
    CONTACT_LINK_USER_PREFIX,
    OUTSIDERS_LINK_USER,
    contact_link_user,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
)
from app.modules.contacts.contracts.visitor_sessions import (
    conversation_has_visitor,
    latest_visitor_conversation,
    visitor_conversation_contact,
)


#: The most outside links one conversation is read for.
_MOST_OUTSIDE_LINKS = 8

#: How a web widget's conversations are named where a platform would be.
WEB_PLATFORM = "WEB"


async def links_to_people_outside(session: AsyncSession, conversation_id: UUID) -> bool:
    """Whether a link names this conversation as answering people outside the pod.

    A group's strangers' thread, one contact's private chat, or a web
    visitor's chat: all are answered as nobody.
    """
    link = AgentSurfaceConversationLinkModel
    linked = (
        select(link.id)
        .where(link.conversation_id == conversation_id)
        .where(
            or_(
                link.external_user_id == OUTSIDERS_LINK_USER,
                link.external_user_id.startswith(
                    CONTACT_LINK_USER_PREFIX, autoescape=True
                ),
            )
        )
        .exists()
    )
    if (await session.execute(select(linked))).scalar():
        return True
    return await conversation_has_visitor(session, conversation_id)


@dataclass(frozen=True, slots=True)
class OutsideLink:
    """Who a link says a conversation answers: one contact, or nobody named."""

    contact_id: UUID | None = None


async def outside_link(
    session: AsyncSession, conversation_id: UUID
) -> OutsideLink | None:
    """What routing's links say about whom this conversation answers.

    ``None`` when no link names it as answering people outside the pod. A
    contact's chat -- on a platform (``~contact:{id}``) or on the web -- names
    the contact, so a conversation that lost its metadata is repaired to that
    contact rather than to a group's anonymous strangers.
    """
    link = AgentSurfaceConversationLinkModel
    users = (
        await session.scalars(
            select(link.external_user_id)
            .where(link.conversation_id == conversation_id)
            .where(
                or_(
                    link.external_user_id == OUTSIDERS_LINK_USER,
                    link.external_user_id.startswith(
                        CONTACT_LINK_USER_PREFIX, autoescape=True
                    ),
                )
            )
            # A conversation has a link or two; more than this many outside
            # links is already a conversation naming nobody (see below).
            .limit(_MOST_OUTSIDE_LINKS)
        )
    ).all()
    on_the_web, web_contact = await visitor_conversation_contact(
        session, conversation_id
    )
    if not users and not on_the_web:
        return None
    contacts = {_contact_in(user) for user in users} | {web_contact}
    contacts.discard(None)
    # Two contacts on one conversation is not something routing writes; name
    # neither rather than pick one, and the run still answers as nobody.
    return OutsideLink(contact_id=contacts.pop() if len(contacts) == 1 else None)


def _contact_in(link_user: str | None) -> UUID | None:
    if link_user is None or not link_user.startswith(CONTACT_LINK_USER_PREFIX):
        return None
    try:
        return UUID(link_user.removeprefix(CONTACT_LINK_USER_PREFIX))
    except ValueError:
        return None


async def latest_contact_thread(
    session: AsyncSession, contact_id: UUID
) -> tuple[UUID, str] | None:
    """The contact's most recent conversation, and the platform it lives on.

    A chat platform's thread (``~contact:{id}`` link), or a web widget session
    (platform ``WEB``), whichever was active last.
    """
    link = AgentSurfaceConversationLinkModel
    linked = (
        await session.execute(
            select(link.conversation_id, link.platform, link.updated_at)
            .where(link.external_user_id == contact_link_user(contact_id))
            .order_by(link.updated_at.desc())
            .limit(1)
        )
    ).first()
    web = await latest_visitor_conversation(session, contact_id)
    if web is not None:
        web_conversation_id, web_seen_at = web
        if linked is None or web_seen_at > linked.updated_at:
            return web_conversation_id, WEB_PLATFORM
    if linked is not None:
        return linked.conversation_id, linked.platform
    return None
