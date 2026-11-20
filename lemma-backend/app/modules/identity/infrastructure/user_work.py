"""Keep what a person said they do on their own preferences.

Only the ``work`` key is written, merged in SQL like ``organization_home_seen``,
so answering never rewrites -- or empties -- a default surface or a Home mark
stored beside it, however the writes interleave.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Text, case, func, literal, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.domain.user_preferences import WorkAnswer
from app.modules.identity.infrastructure.models.user_models import User

_KEY = "work"


async def record_work(
    session: AsyncSession, *, user_id: UUID, answer: WorkAnswer
) -> bool:
    """Store the answer, replacing any earlier one. False when there is no such user.

    A missing or JSON-null blob starts empty; stored preferences that are not
    an object hold nothing another reader could use, and are replaced.
    """
    empty = literal({}, JSONB)
    base = case(
        (func.jsonb_typeof(User.preferences) == "object", User.preferences),
        else_=empty,
    )
    merged = func.jsonb_set(
        base,
        literal([_KEY], ARRAY(Text)),
        literal(answer.model_dump(mode="json"), JSONB),
    )
    written = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(preferences=merged)
        .returning(User.id)
    )
    return written.scalar_one_or_none() is not None
