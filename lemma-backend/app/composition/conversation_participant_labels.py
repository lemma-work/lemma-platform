"""Names for the people in a conversation.

In the composition root because it spans two modules: the roster belongs to the
agent module and the names belong to identity, and neither may reach into the
other's tables. One query rather than a read per participant -- this is on the
path that opens a conversation, and the transcript cannot attribute a turn
until it has come back.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.infrastructure.models.user_models import User


async def read_user_labels(
    session: AsyncSession, user_ids: list[UUID]
) -> dict[UUID, str]:
    """What to call each of these people: a name, or an email.

    Missing ids are simply absent from the result. A caller that gets nothing
    back decides for itself whether "Unknown" or an unlabelled row reads better
    in its own context, which this cannot know.
    """
    if not user_ids:
        return {}
    rows = await session.execute(
        select(User.id, User.first_name, User.last_name, User.email).where(
            User.id.in_(user_ids)
        )
    )
    labels: dict[UUID, str] = {}
    for user_id, first_name, last_name, email in rows:
        person = " ".join(part for part in (first_name, last_name) if part).strip()
        label = person or (email or "")
        if label:
            labels[user_id] = label
    return labels
