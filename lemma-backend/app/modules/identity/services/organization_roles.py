"""Write the role assignment that makes an organization membership mean something.

An organization permission is answered from ``RoleAssignment`` rows alone
(``AuthorizationDataService.build_context``); the ``role`` column on the
membership row is what lists and checks inside identity read, and nothing
derives the one from the other. Every path that makes a membership has to write
both, or the member holds a role that grants nothing -- an organization made by
a chat sign-up left its own owner unable to make a pod in it.

The assignment is read off the membership row rather than passed in, so the two
cannot disagree. A caller that joins somebody who is already a member, or
accepts a pod invitation for an owner, rewrites the role they hold instead of
replacing it with the one the request happened to name.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.service import AuthorizationDataService
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)


async def sync_organization_role(
    session: AsyncSession,
    *,
    organization_id: UUID,
    user_id: UUID,
    assigned_by_user_id: UUID | None,
) -> None:
    """Make the member's role assignment match their membership row.

    Nothing to do when they hold no membership: a refused join leaves none.
    """
    membership = (
        await session.execute(
            select(OrganizationMember.id, OrganizationMember.role).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
        )
    ).one_or_none()
    if membership is None:
        return
    await AuthorizationDataService(session).assign_roles(
        organization_id=organization_id,
        pod_id=None,
        principal_type="ORG_MEMBER",
        principal_id=membership.id,
        role_names=[OrganizationRole(membership.role).value],
        assigned_by_user_id=assigned_by_user_id,
    )
