"""Recording that somebody acted in a thread without writing a message to it.

A tapped button is inbound activity as far as the platform is concerned --
WhatsApp opens its 24-hour reply window on any message the person sends, and a
button reply is one -- but the interaction path never went through
``update_last_event``, which is the message path's write. So a person who
answered only by tapping looked silent, and the reply their tap resumed could
be refused as outside the window they had just reopened.

A function beside the link repository rather than a method on it: that file
sits at the size ceiling, and this is one statement with no reads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
)


async def touch_inbound(
    session: AsyncSession, *, link_id: UUID, at: datetime | None = None
) -> None:
    """Stamp ``last_inbound_at`` on one link, and nothing else on it.

    ``last_event`` is deliberately left alone: it is the message replies are
    threaded to, and an interaction payload is not a message one can reply to.
    """
    await session.execute(
        update(AgentSurfaceConversationLinkModel)
        .where(AgentSurfaceConversationLinkModel.id == link_id)
        .values(last_inbound_at=at or datetime.now(timezone.utc))
    )


__all__ = ["touch_inbound"]
