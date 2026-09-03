"""The two reads behind "would this leave the pod with nobody who can run it".

They live together because they answer one question from two sides, and apart
from ``pod_repositories`` because that file is at the size ratchet. The rule
they serve -- PS-POD-041 -- is stated once in
``PodMemberService._refuse_if_last_administrator``; these are its facts.

Both go by *permission*, never by role name. A pod may hand ``pod.member.manage``
to a custom role, and its holder administers the pod as surely as a POD_ADMIN
does. Counting names is how the guard came to fire for the wrong people and stay
silent for the right ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_ as sa_and
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.models import (
    RoleAssignmentModel,
    RoleModel,
    RolePermissionModel,
)
from app.modules.pod.infrastructure.models import PodMember


async def roles_grant_permission(
    session: AsyncSession,
    *,
    pod_id: UUID,
    role_names: Sequence[str],
    permission_id: str,
) -> bool:
    """Whether any of ``role_names`` in this pod carries ``permission_id``.

    Knowing how many administrators a pod has is only half of "would this change
    leave it with none". The other half is whether the roles a member is about
    to be left with still administer it -- which, for a custom role, only its
    permissions can answer.
    """
    if not role_names:
        return False
    stmt = (
        select(RoleModel.id)
        .join(RolePermissionModel, RolePermissionModel.role_id == RoleModel.id)
        .where(
            RoleModel.pod_id == pod_id,
            RoleModel.name.in_(list(role_names)),
            RolePermissionModel.permission_id == permission_id,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def count_members_who_can(
    session: AsyncSession,
    *,
    pod_id: UUID,
    permission_id: str,
) -> int:
    """How many members of ``pod_id`` hold a role granting ``permission_id``.

    Two more things this does not do, each of which was a way to get the wrong
    number.

    It counts distinct members, not assignment rows -- a member assigned the
    same role twice is one administrator, and counting rows would have inflated
    the total and silently disarmed the guard it feeds.

    It takes ``FOR UPDATE`` on the rows it counted. The callers are
    check-then-act: two administrators leaving at the same moment would each see
    the other and both be allowed through, which is precisely the state the
    guard exists to prevent. Locking the members serialises them.

    The de-duplication happens here rather than in SQL, and that is not a
    stylistic choice: Postgres rejects ``SELECT DISTINCT ... FOR UPDATE``
    outright, so asking for both in one statement raised
    ``FeatureNotSupportedError`` and the guard answered 500 every time it
    actually fired. It never showed up because the only scenario covering the
    rule demoted an *organization owner*, who is exempt and returns before
    reaching this query. See DEV-POD-002.
    """
    stmt = (
        select(PodMember.id)
        .join(
            RoleAssignmentModel,
            sa_and(
                RoleAssignmentModel.principal_type == "POD_MEMBER",
                RoleAssignmentModel.principal_id == PodMember.id,
            ),
        )
        .join(RoleModel, RoleModel.id == RoleAssignmentModel.role_id)
        .join(RolePermissionModel, RolePermissionModel.role_id == RoleModel.id)
        .where(
            PodMember.pod_id == pod_id,
            RolePermissionModel.permission_id == permission_id,
        )
        .with_for_update(of=PodMember)
    )
    return len(set((await session.execute(stmt)).scalars().all()))
