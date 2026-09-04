"""What is left of identity's cross-module read adapters.

Four of the six importers are gone, and with them the two operations that had
somewhere better to be: the email lookup was already published as
`identity/contracts/profiles.py::user_profile`, and the `UserReader` builder was
already published as `identity/contracts/organizations.py::build_user_directory`
-- differing only in a `message_bus` the unit of work documents as a no-op.

The two below are the same organization-membership read asked twice, and they
belong beside `build_organization_membership` and `organization_member_count` in
`identity/contracts/organizations.py`. One operation should replace them --
`organization_member_role(uow, user_id, organization_id) -> OrganizationRole |
None` -- leaving `usage` to name the roles that may read usage, which is usage's
policy, not identity's vocabulary.

Remaining importers: `app/modules/usage/api/controllers.py` and
`app/modules/agent/api/controllers/runtime_config_controller.py`.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)


async def user_can_view_organization_usage(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> bool:
    member = await OrganizationRepository(uow).get_member(user_id, organization_id)
    return bool(
        member
        and member.role in {OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR}
    )


async def user_is_organization_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> bool:
    return (
        await OrganizationRepository(uow).get_member(user_id, organization_id)
        is not None
    )
