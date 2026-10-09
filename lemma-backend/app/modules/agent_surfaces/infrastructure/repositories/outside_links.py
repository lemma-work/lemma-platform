"""Whether routing linked a conversation to people outside the pod.

Its own module because it is asked from both sides of the boundary -- the
agent's run deciding whose authority it has, and a question being passed on --
and because it must recognise every kind of outside link: a group's strangers'
thread, each contact's private chat, and a web widget visitor's chat.
"""

from __future__ import annotations

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
)


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
