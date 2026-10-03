"""Which of a person's organizations is "theirs", asked one way everywhere."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)


async def oldest_membership(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID
) -> OrganizationMember | None:
    """The organization this person joined first: the one "their own" means.

    By when they joined, with the id as the tiebreak. First-workspace selection
    used to pick by organization id, and ids are random -- so it and
    `preferred_organization_membership` could answer "which is their
    organization" differently for the same person, and a chat signup put a new
    pod somewhere other than where the next one would go.

    Its own module, not either caller's: the identity contract imports the
    service layer's dependencies transitively, and a contract importing a
    service would close an import cycle through `pod`.
    """
    return await uow.session.scalar(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == user_id)
        .order_by(OrganizationMember.created_at, OrganizationMember.id)
        .limit(1)
    )
