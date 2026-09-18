"""Reading a principal's roles and the permissions behind them.

The one place that knows how ``roles``, ``role_assignments`` and
``role_permissions`` join, and how their rows collapse into the three sets a
``Context`` carries. Everything that answers "what may this principal do" starts
here: ``AuthorizationDataService`` for one principal at a time, and
``listing_contexts`` for a whole organization at once.

One module because the two used to be two copies. A second spelling of this join
is not a second query, it is a second answer to the same question -- and the one
that drifts is the one nobody is looking at. The same reasoning is written out
at ``sql_actions.allowed_actions_expr``, where the SQL projection and the
in-process ladder have to agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.models import (
    RoleAssignmentModel,
    RoleModel,
    RolePermissionModel,
)

#: One row of the join: the principal it was assigned to, the role, one of that
#: role's permissions -- or ``None`` where the role grants none, which the outer
#: join preserves so a role with no permissions still names itself -- and the
#: pod the role itself is scoped to, ``None`` for an organization-level role.
#:
#: The role's own pod is carried because a caller spanning pods has to re-check
#: it: see :func:`roles_applying_to_pod`.
RoleRow = tuple[UUID, UUID, str, str | None, UUID | None]


class _AnyPodScope:
    """Sentinel for ``pod_scope``. See ``ANY_POD``."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "ANY_POD"


#: Take a principal's roles whatever pod they are scoped to, organization-level
#: roles included.
#:
#: A distinct value rather than ``pod_id=None``, which already means "the
#: organization's own roles, and no pod's". Collapsing the two is how a caller
#: spanning several pods silently gets org roles only -- every pod-scoped
#: permission missing, and a listing that authorizes correctly against nothing.
ANY_POD = _AnyPodScope()


async def load_roles_for_principals(
    session: AsyncSession,
    *,
    principal_ids: Sequence[UUID],
    organization_id: UUID,
    pod_scope: UUID | None | _AnyPodScope,
    principal_type: str | None = None,
) -> list[RoleRow]:
    """Roles and permissions for any number of principals, in one query.

    Rows lead with the principal id so a caller holding several can split them
    back out without a query each.

    ``pod_scope`` is required, because every value of it is a different answer:
    a pod id takes that pod's roles, ``None`` takes the organization's own, and
    :data:`ANY_POD` takes both.

    :data:`ANY_POD` does no scoping at all, so a caller using it **must** put
    each row back in its place with :func:`roles_applying_to_pod` before
    merging. Matching on the principal is not enough on its own: a pod-member
    id is unique to its pod, but an organization-member id is not, and a caller
    spanning pods merges that one principal's rows into every pod it builds.
    """
    if not principal_ids:
        return []
    statement = (
        select(
            RoleAssignmentModel.principal_id,
            RoleModel.id,
            RoleModel.name,
            RolePermissionModel.permission_id,
            RoleModel.pod_id,
        )
        .join(RoleAssignmentModel, RoleAssignmentModel.role_id == RoleModel.id)
        .join(
            RolePermissionModel,
            RolePermissionModel.role_id == RoleModel.id,
            isouter=True,
        )
        .where(
            RoleAssignmentModel.principal_id.in_(principal_ids),
            RoleModel.organization_id == organization_id,
        )
    )
    if principal_type is not None:
        statement = statement.where(
            RoleAssignmentModel.principal_type == principal_type
        )
    if not isinstance(pod_scope, _AnyPodScope):
        statement = statement.where(RoleModel.pod_id == pod_scope)
    return list((await session.execute(statement)).all())


def merge_role_data(
    rows: Iterable[RoleRow],
    role_ids: set[UUID],
    role_names: set[str],
    permission_ids: set[str],
) -> None:
    """Collapse rows into the three sets a ``Context`` carries, in place.

    Scoping is the caller's, not this function's: rows fetched with
    :data:`ANY_POD` go through :func:`roles_applying_to_pod` first.
    """
    for _principal_id, role_id, role_name, permission_id, _role_pod_id in rows:
        role_ids.add(role_id)
        role_names.add(role_name)
        if permission_id is not None:
            permission_ids.add(permission_id)


def roles_applying_to_pod(rows: Iterable[RoleRow], pod_id: UUID) -> list[RoleRow]:
    """The rows that really do govern ``pod_id``.

    An organization-level role (no pod of its own) applies in every pod; a role
    scoped to a pod applies in that one. Anything else is a role belonging to a
    different pod, and it is dropped.

    This restores, at merge time, the constraint the per-pod query used to
    carry in its WHERE clause. It matters most for the organization member:
    that one principal's rows are merged into *every* pod a listing builds, so
    a pod-scoped role reaching it would be granted in all of them. Nothing in
    the schema forbids such an assignment -- ``role_assignments`` has no
    constraint tying a principal's scope to its role's -- and `assign_roles`
    only avoids writing one because it resolves roles by (organization, pod)
    first. That is a property of one caller, which is thinner than a check.
    """
    return [row for row in rows if row[4] is None or row[4] == pod_id]
