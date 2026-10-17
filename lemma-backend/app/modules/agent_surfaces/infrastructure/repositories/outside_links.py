"""Where a contact's conversations are, read off routing's links.

A contact's conversation is keyed ``~contact:{id}`` on the platform thread it
lives on -- a private chat, an email thread, or a group -- and a web visitor's
lives on their session instead. Whom a conversation answers is the
conversation's own ``contact_id``; this only finds where it is.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.groups import contact_link_user
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
)
from app.modules.contacts.contracts.visitor_sessions import (
    latest_visitor_conversation,
)

#: How a web widget's conversations are named where a platform would be.
WEB_PLATFORM = "WEB"

#: The link kinds that are somewhere private to the contact. A group is where
#: they also speak, never where the pod writes to them first.
_PRIVATE_KINDS = ("DM", "EMAIL")


async def latest_contact_thread(
    session: AsyncSession, contact_id: UUID
) -> tuple[UUID, str] | None:
    """The contact's most recent private conversation, and the platform it lives on.

    A chat platform's private thread or email thread (``~contact:{id}`` link),
    or a web widget session (platform ``WEB``), whichever was active last.
    Never a group's: what a member writes first to a contact goes to them
    alone.
    """
    link = AgentSurfaceConversationLinkModel
    linked = (
        await session.execute(
            select(link.conversation_id, link.platform, link.updated_at)
            .where(
                link.external_user_id == contact_link_user(contact_id),
                link.conversation_kind.in_(_PRIVATE_KINDS),
            )
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
