"""When a member last looked at their organization's Home.

Home says what moved since the member last looked. The moment used to live in
the browser's storage, so a second browser, a phone, or a cleared profile said
everything was new. It is kept in the person's own preferences now
(``UserPreferences.home_seen``), one entry per organization, read and set only
by that person and only while they are a member.

It only moves forward. A tab left open since yesterday that marks Home seen as
of when it loaded must not undo what a newer tab already marked, so the write
keeps the later of the stored moment and the new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.domain.user_preferences import UserPreferences
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.identity.infrastructure.models.user_models import User


@dataclass(frozen=True, slots=True)
class HomeSeen:
    #: None until the member first marks it.
    seen_at: datetime | None
    #: What it was before this write; equal to ``seen_at`` on a read.
    previous_seen_at: datetime | None


def _preferences(raw: object) -> UserPreferences:
    """Stored preferences, or none: a value this model no longer reads is
    treated as empty rather than failing the page that asked."""
    if not raw:
        return UserPreferences()
    try:
        return UserPreferences.model_validate(raw)
    except ValidationError:
        return UserPreferences()


async def _preferences_of_member(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID, lock: bool
) -> UserPreferences | None:
    """The member's preferences, or None when they are not a member."""
    stmt = select(User.preferences).where(
        User.id == user_id,
        exists().where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        ),
    )
    if lock:
        stmt = stmt.with_for_update(of=User)
    row = (await session.execute(stmt)).one_or_none()
    return None if row is None else _preferences(row.preferences)


async def read_home_seen(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID
) -> HomeSeen | None:
    """The member's mark. None when they are not a member."""
    preferences = await _preferences_of_member(
        session, user_id=user_id, organization_id=organization_id, lock=False
    )
    if preferences is None:
        return None
    seen_at = preferences.home_seen.get(organization_id)
    return HomeSeen(seen_at=seen_at, previous_seen_at=seen_at)


async def mark_home_seen(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID, at: datetime
) -> HomeSeen | None:
    """Move the member's mark to ``at``, unless it is already later.

    The user's row is locked for the read of what it was, so two tabs marking
    at once each see the other's write rather than both reporting the same
    previous moment. ``updated_at`` is left alone: looking at a page is not a
    change to the account.
    """
    preferences = await _preferences_of_member(
        session, user_id=user_id, organization_id=organization_id, lock=True
    )
    if preferences is None:
        return None
    previous = preferences.home_seen.get(organization_id)
    marked = preferences.with_home_seen(organization_id, at)
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            preferences=marked.model_dump(mode="json"),
            updated_at=User.updated_at,
        )
    )
    return HomeSeen(
        seen_at=marked.home_seen[organization_id], previous_seen_at=previous
    )
