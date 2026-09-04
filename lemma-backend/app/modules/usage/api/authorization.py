"""Who may ask for whose usage.

Two gates, deliberately different. An organization's numbers are an
administrative view and stay behind owner/editor. A person's own numbers are
not: `PS-OPS-002` says somebody can see their own spend "without requiring
administrative access", and the people most likely to run into a limit are
exactly the ones who are not administrators.

Identity answers what a person's role *is*; the policy naming what that role may
do here belongs to usage, and lives here where usage can see it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.api.dependencies import UoWDep
from app.modules.identity.contracts import (
    AuthenticatedUser as UserEntity,
    OrganizationRole,
)
from app.modules.identity.contracts.organizations import organization_member_role
from app.modules.usage.domain.errors import UsageAccessDeniedError
<<<<<<< HEAD

#: Which organization roles may read an organization's spend.
_ROLES_THAT_MAY_READ_USAGE = frozenset(
    {OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR}
)
=======
from app.modules.usage.services.identity_lookups import identity_lookups
>>>>>>> 29cc0441b (Warn before the allowance runs out, instead of only refusing when it has)


async def require_usage_org_access(
    *,
    user: UserEntity,
    organization_id: UUID,
    uow: UoWDep,
) -> None:
    """Administration, for the whole organization's spend."""
<<<<<<< HEAD
    role = await organization_member_role(
=======
    can_view = identity_lookups().can_view_organization_usage
    if not await can_view(
>>>>>>> 29cc0441b (Warn before the allowance runs out, instead of only refusing when it has)
        uow,
        user_id=user.id,
        organization_id=organization_id,
    )
    if role not in _ROLES_THAT_MAY_READ_USAGE:
        raise UsageAccessDeniedError(
            "Only organization owners and editors can view usage"
        )


async def require_usage_org_membership(
    *,
    user: UserEntity,
    organization_id: UUID,
    uow: UoWDep,
) -> None:
    """Membership, for one's own spend.

    Any role at all rather than a set of them: `organization_member_role`
    answers ``None`` for somebody outside the organization, and every role
    inside it may see their own figures.

    Only ever widens who may ask about *themselves*: every route behind this
    gate forces ``user_id`` to the caller, so a member still cannot read
    anybody else's figures, and a non-member is still refused outright.
    """
<<<<<<< HEAD
    role = await organization_member_role(
=======
    is_member = identity_lookups().is_organization_member
    if not await is_member(
>>>>>>> 29cc0441b (Warn before the allowance runs out, instead of only refusing when it has)
        uow,
        user_id=user.id,
        organization_id=organization_id,
    )
    if role is None:
        raise UsageAccessDeniedError("Only organization members can view their usage")
