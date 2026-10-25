"""When a member last looked at their organization's Home.

Home says what moved since the member last looked. The moment used to live in
the browser's storage, so a second browser, a phone, or a cleared profile said
everything was new. It is a column on the member's own row now: one per
person per organization, read and set only by that person.

It only moves forward. A tab left open since yesterday that marks Home seen as
of when it loaded must not undo what a newer tab already marked, so the write
keeps the later of the stored moment and the new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)


@dataclass(frozen=True, slots=True)
class HomeSeen:
    #: None until the member first marks it.
    seen_at: datetime | None
    #: What it was before this write; equal to ``seen_at`` on a read.
    previous_seen_at: datetime | None


def _member(
    *, user_id: UUID, organization_id: UUID
) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return (
        OrganizationMember.user_id == user_id,
        OrganizationMember.organization_id == organization_id,
    )


async def read_home_seen(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID
) -> HomeSeen | None:
    """The member's mark. None when they are not a member."""
    row = (
        await session.execute(
            select(OrganizationMember.id, OrganizationMember.home_seen_at).where(
                *_member(user_id=user_id, organization_id=organization_id)
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return HomeSeen(seen_at=row.home_seen_at, previous_seen_at=row.home_seen_at)


async def mark_home_seen(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID, at: datetime
) -> HomeSeen | None:
    """Move the member's mark to ``at``, unless it is already later.

    The row is locked for the read of what it was, so two tabs marking at once
    each see the other's write rather than both reporting the same previous
    moment. ``updated_at`` is left alone: looking at a page is not a change to
    the membership.
    """
    previous = (
        await session.execute(
            select(OrganizationMember.id, OrganizationMember.home_seen_at)
            .where(*_member(user_id=user_id, organization_id=organization_id))
            .with_for_update()
        )
    ).one_or_none()
    if previous is None:
        return None
    seen_at = (
        await session.execute(
            update(OrganizationMember)
            .where(OrganizationMember.id == previous.id)
            .values(
                home_seen_at=func.greatest(
                    func.coalesce(OrganizationMember.home_seen_at, at), at
                ),
                updated_at=OrganizationMember.updated_at,
            )
            .returning(OrganizationMember.home_seen_at)
        )
    ).scalar_one()
    return HomeSeen(seen_at=seen_at, previous_seen_at=previous.home_seen_at)
