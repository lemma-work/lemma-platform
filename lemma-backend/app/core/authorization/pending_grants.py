"""Grants waiting for the resource they name.

A grant row names its resource by id, so a grant on something the pod does not
have yet cannot be written. A bundle import meets exactly that: a function
bundled with a grant on an optional connector the importing organization has
never installed is imported without the connector. Dropping the grant left the
function refused once somebody connected the connector later, with nothing in
the pod saying why. So the grant waits here instead, under a key the resource's
own module chooses (``resource_key``), and becomes an ordinary grant when that
module says the resource now exists (:func:`settle_pending_grants`).

Holding one is a grant write, and bounded like one: the person holding it must
hold the permissions themselves (:func:`replace_grantee_pending_grants`). The
resource does not exist yet, so the bound is on the permission rather than on
the resource; the module that settles a key decides which resources it may
settle onto.

A deleted grantee's pending grants go with its grants
(`grants.delete_grantee_grants`), and a deleted pod's with the pod.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.conferral import CONFERRAL_EXCEEDS_HOLDER, assert_can_confer
from app.core.authorization.context import Context, ResourceType
from app.core.authorization.grants import replace_resource_grantee_grant
from app.core.authorization.models import PendingResourceGrantModel
from app.core.authorization.permission_rows import ensure_permission_rows
from app.core.domain.errors import DomainError

__all__ = [
    "PendingGrant",
    "replace_grantee_pending_grants",
    "settle_pending_grants",
]


#: A key waits for a few workloads' handful of permissions. Past this many
#: rows the rest stay waiting, for the next resource of that key.
_MAX_SETTLED_ROWS = 1000


@dataclass(frozen=True)
class PendingGrant:
    """Permissions on a resource known only by its key, for one grantee."""

    resource_type: ResourceType
    resource_key: str
    permission_ids: tuple[str, ...]


async def replace_grantee_pending_grants(
    session: AsyncSession,
    *,
    ctx: Context,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    grants: Sequence[PendingGrant],
    created_by_user_id: UUID | None,
    action: str = "grant access you could not use yourself",
) -> None:
    """Replace everything ``grantee_id`` is waiting for in the pod with ``grants``.

    Replace, like ``replace_grantee_resource_grants``: what the writer lists
    is the whole set, so a grant the grantee no longer needs stops waiting.
    """
    requested = {p for grant in grants for p in grant.permission_ids}
    if requested and ctx.is_named_workload:
        raise DomainError(
            "An agent or function may not change who can do what.",
            code=CONFERRAL_EXCEEDS_HOLDER,
            status_code=403,
        )
    assert_can_confer(ctx, requested, action=action)
    await session.execute(
        delete(PendingResourceGrantModel).where(
            PendingResourceGrantModel.pod_id == pod_id,
            PendingResourceGrantModel.grantee_type == grantee_type,
            PendingResourceGrantModel.grantee_id == grantee_id,
        )
    )
    rows = {
        (grant.resource_type.value, grant.resource_key, permission_id)
        for grant in grants
        for permission_id in grant.permission_ids
    }
    await ensure_permission_rows(session, {row[2] for row in rows})
    for resource_type, resource_key, permission_id in sorted(rows):
        session.add(
            PendingResourceGrantModel(
                pod_id=pod_id,
                resource_type=resource_type,
                resource_key=resource_key,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                permission_id=permission_id,
                created_by_user_id=created_by_user_id,
            )
        )
    await session.flush()


async def settle_pending_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_key: str,
    resource_id: UUID,
) -> int:
    """Grant on ``resource_id`` what was waiting for ``resource_key``; how many
    grantees were granted.

    Called by the resource's module in the transaction that creates the
    resource, so the grants and the resource commit or roll back together. The
    grants were bounded when they were held; nothing is asked of whoever
    happens to create the resource, who may be a workload on its first use.
    """
    found = await session.execute(
        select(
            PendingResourceGrantModel.id,
            PendingResourceGrantModel.grantee_type,
            PendingResourceGrantModel.grantee_id,
            PendingResourceGrantModel.permission_id,
            PendingResourceGrantModel.created_by_user_id,
        )
        .where(
            PendingResourceGrantModel.pod_id == pod_id,
            PendingResourceGrantModel.resource_type == resource_type.value,
            PendingResourceGrantModel.resource_key == resource_key,
        )
        .order_by(PendingResourceGrantModel.id)
        .limit(_MAX_SETTLED_ROWS)
    )
    settled: list[UUID] = []
    permissions: dict[tuple[str, UUID], set[str]] = {}
    granted_by: dict[tuple[str, UUID], UUID | None] = {}
    for row_id, grantee_type, grantee_id, permission_id, created_by in found.all():
        settled.append(row_id)
        permissions.setdefault((grantee_type, grantee_id), set()).add(permission_id)
        granted_by[(grantee_type, grantee_id)] = created_by
    for grantee_type, grantee_id in sorted(
        permissions, key=lambda g: (g[0], str(g[1]))
    ):
        await replace_resource_grantee_grant(
            session,
            pod_id=pod_id,
            resource_type=resource_type,
            resource_id=resource_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission_ids=sorted(permissions[(grantee_type, grantee_id)]),
            created_by_user_id=granted_by[(grantee_type, grantee_id)],
        )
    if settled:
        await session.execute(
            delete(PendingResourceGrantModel).where(
                PendingResourceGrantModel.id.in_(settled)
            )
        )
        await session.flush()
    return len(permissions)
