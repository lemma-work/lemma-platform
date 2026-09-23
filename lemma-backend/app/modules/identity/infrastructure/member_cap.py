"""Whether an organization has room for one more person.

Called from the two writes every way into an organization passes through --
``add_member`` and ``add_invitation`` -- so a join path added later is held to
the plan without knowing the plan exists. Checked at the write, not in each
service, because there are five services that add members and one of them lives
in another module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.plan_limits import build_plan_limits
from app.modules.identity.domain.errors import OrganizationMemberLimitError
from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationStatus,
)
from app.modules.identity.infrastructure.models.organization_models import (
    Organization,
    OrganizationInvitation,
    OrganizationMember,
)


async def lock_organization_seats(
    uow: SqlAlchemyUnitOfWork, organization_id: UUID
) -> None:
    """Hold the organization's headcount until the transaction ends.

    Taken before anything about who is in the organization changes -- an
    invitation accepted, a member added -- so every change to the count and
    every read of it happen one at a time. Re-taking it in the same
    transaction is free.
    """
    await uow.session.execute(
        select(Organization.id)
        .where(Organization.id == organization_id)
        .with_for_update()
    )


async def refuse_if_organization_full(
    uow: SqlAlchemyUnitOfWork, organization_id: UUID
) -> None:
    """Raise `OrganizationMemberLimitError` if one more person would not fit.

    One more person is one more member or one more invitation. Accepting an
    invitation takes up the seat it already holds, which is why
    `OrganizationService.accept_invitation` locks the seats, then saves the
    invitation as accepted *before* adding the member: in the other order the
    count sees the person twice, and an organization at its cap refuses an
    invitation it sent.

    The organization row is locked for the rest of the transaction, so two
    invitations sent at once cannot both take the last seat.
    """
    plan_limits = build_plan_limits(uow)
    if plan_limits is None:
        return
    limit = await plan_limits.member_limit(organization_id=organization_id)
    if limit is None:
        return

    session = uow.session
    await lock_organization_seats(uow, organization_id)
    members = (
        select(func.count())
        .select_from(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
    )
    invited = (
        select(func.count())
        .select_from(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.status == OrganizationInvitationStatus.PENDING,
            OrganizationInvitation.expires_at > datetime.now(timezone.utc),
        )
    )
    used = int((await session.execute(members)).scalar_one()) + int(
        (await session.execute(invited)).scalar_one()
    )
    if used + 1 > limit:
        raise OrganizationMemberLimitError(limit=limit, used=used)
