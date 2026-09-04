"""The person behind a user id, for anything that renders their name.

Replaces the `User` half of `app/composition/agent_context_models.py`, which
published the ORM class so `agent` could `select(User)` and read four columns
off the row. Publishing the columns instead means a rename inside identity is
identity's to fix, and that adding a column does not widen what another module
can see.

Deliberately not the whole user: an address, a display name and a timezone are
what a brief needs, and a contract that returned everything would make every
future field part of this surface by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.modules.identity.infrastructure.models.user_models import User


@dataclass(frozen=True, slots=True)
class UserProfileRef:
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    timezone: str | None = None


async def user_profile(session, user_id: UUID) -> UserProfileRef | None:
    """This user's profile, or ``None`` when there is no such user.

    ``None`` rather than a raise: the callers render something a person reads,
    and a brief without a name is still worth showing.
    """
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        return None
    return UserProfileRef(
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        timezone=user.timezone,
    )


__all__ = ["UserProfileRef", "user_profile"]
