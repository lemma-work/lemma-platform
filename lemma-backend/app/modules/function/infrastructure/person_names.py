"""The name a function run tells its code the person goes by."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.contracts.profiles import user_profile


async def person_name(session: AsyncSession, user_id: UUID | None) -> str | None:
    """The member's name as Lemma shows it, or ``None`` with no member or name.

    Read as a run starts rather than kept on the run: a name is the person's to
    change, and a function should greet them by the current one.
    """
    if user_id is None:
        return None
    profile = await user_profile(session, user_id)
    if profile is None:
        return None
    name = " ".join(part for part in (profile.first_name, profile.last_name) if part)
    return name or None


__all__ = ["person_name"]
