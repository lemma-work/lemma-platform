"""Whether routing linked a conversation to people outside the pod.

Its own module because it is asked from both sides of the boundary -- the
agent's run deciding whose authority it has, and a question being passed on --
and because it must recognise every kind of outside link: a group's strangers'
thread, and each contact's private chat.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.groups import (
    CONTACT_LINK_USER_PREFIX,
    OUTSIDERS_LINK_USER,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
)


async def links_to_people_outside(session: AsyncSession, conversation_id: UUID) -> bool:
    """Whether a link names this conversation as answering people outside the pod.

    A group's strangers' thread, or one contact's private chat: both are
    answered as nobody.
    """
    link = AgentSurfaceConversationLinkModel
    statement = select(
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
    return bool((await session.execute(statement)).scalar())
