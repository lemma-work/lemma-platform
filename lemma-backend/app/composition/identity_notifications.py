"""One membership question, left here because its last caller is not ours to edit.

Five of the six importers are gone. The operations that had somewhere better to
be went there: the email lookup was already published as
`identity/contracts/profiles.py::user_profile`, the `UserReader` builder as
`identity/contracts/organizations.py::build_user_directory`, and the two
organization-membership reads are now the single
`identity/contracts/organizations.py::organization_member_role`, which answers
what a person's role *is* and leaves each caller to name what that role may do.
`usage` took its half of that policy back into
`usage/api/controllers.py::_ROLES_THAT_MAY_READ_USAGE`.

What is below is the same question with `role is not None` as the policy, and it
is a one-line change at its call site --
`agent/api/controllers/runtime_config_controller.py:54` -- which belongs to
another change. Until then this forwards rather than duplicating the read, so
there is one membership query in the tree and not two.

Remaining importer: `app/modules/agent/api/controllers/runtime_config_controller.py`.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.contracts.organizations import organization_member_role


async def user_is_organization_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> bool:
    return (
        await organization_member_role(
            uow, user_id=user_id, organization_id=organization_id
        )
        is not None
    )
