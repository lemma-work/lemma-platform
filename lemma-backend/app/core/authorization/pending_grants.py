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

Holding and settling one key take the same transaction lock
(:func:`lock_pending_key`), so neither reads past the other: two declarations
of a resource at once settle a grant onto one of them, and a hold racing a
declaration either lands before it and is settled by it, or after it and sees
it (the resource's module then settles at once).

A deleted grantee's pending grants go with its grants
(:func:`delete_grantee_pending_grants`), and a deleted pod's with the pod.

Every access first asks whether the table exists (:func:`_table_ready`): code
shipped with migration 0077 also runs against the schema before it, where a
hold does nothing and there is nothing to settle or delete.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.conferral import CONFERRAL_EXCEEDS_HOLDER, assert_can_confer
from app.core.authorization.context import Context, ResourceType
from app.core.authorization.models import PendingResourceGrantModel
from app.core.authorization.permission_rows import ensure_permission_rows
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "PendingGrant",
    "delete_grantee_pending_grants",
    "forget_grantee_pending_grants",
    "lock_grantee_pending_keys",
    "lock_pending_key",
    "pending_lock_name",
    "replace_grantee_pending_grants",
    "settle_pending_grants",
]

#: Rows settled per read. A key waits for a few workloads' handful of
#: permissions; the bound is per query, and settling reads until none are left.
_SETTLE_BATCH = 500

_TABLE = PendingResourceGrantModel.__tablename__


class _Schema:
    """Whether this process has seen the table: once it has, it stays."""

    ready = False


async def _table_ready(session: AsyncSession) -> bool:
    if _Schema.ready:
        return True
    _Schema.ready = bool(
        await session.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": _TABLE})
    )
    return _Schema.ready


@dataclass(frozen=True)
class PendingGrant:
    """Permissions on a resource known only by its key, for one grantee."""

    resource_type: ResourceType
    resource_key: str
    permission_ids: tuple[str, ...]


async def lock_pending_key(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_key: str,
) -> None:
    """Hold this transaction's lock on one key until it commits.

    The session is not marked as holding it (`transaction_locks`): each
    read-then-write under this lock finishes inside the function that took
    it, so a later early commit only releases a lock nobody needs any more.
    Marking it changed when a first use's declaration commits.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": pending_lock_name(pod_id, resource_type, resource_key)},
    )


def pending_lock_name(
    pod_id: UUID, resource_type: ResourceType, resource_key: str
) -> str:
    """The text a key's advisory lock is hashed from (``hashtextextended``)."""
    return f"pending_grant:{pod_id}:{resource_type.value}:{resource_key}"


async def lock_grantee_pending_keys(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    grantee_type: str,
    grantee_id: UUID,
    also: Iterable[str] = (),
) -> None:
    """Lock every key of ``resource_type`` ``grantee_id`` waits for, and
    ``also``, in one order.

    Taken before a grantee's grants are replaced, so a settlement of any of
    them waits for the replacement to commit and then finds the rows it
    removed gone, rather than granting from rows read before.
    """
    if not await _table_ready(session):
        return
    keys = set(also)
    after = ""
    while True:
        page = (
            (
                await session.execute(
                    select(PendingResourceGrantModel.resource_key)
                    .where(
                        PendingResourceGrantModel.pod_id == pod_id,
                        PendingResourceGrantModel.resource_type == resource_type.value,
                        PendingResourceGrantModel.grantee_type == grantee_type,
                        PendingResourceGrantModel.grantee_id == grantee_id,
                        PendingResourceGrantModel.resource_key > after,
                    )
                    .distinct()
                    .order_by(PendingResourceGrantModel.resource_key)
                    .limit(_SETTLE_BATCH)
                )
            )
            .scalars()
            .all()
        )
        keys.update(page)
        if len(page) < _SETTLE_BATCH:
            break
        after = page[-1]
    for resource_key in sorted(keys):
        await lock_pending_key(
            session,
            pod_id=pod_id,
            resource_type=resource_type,
            resource_key=resource_key,
        )


async def replace_grantee_pending_grants(
    session: AsyncSession,
    *,
    ctx: Context,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    resource_type: ResourceType,
    grants: Sequence[PendingGrant],
    created_by_user_id: UUID | None,
    action: str = "grant access you could not use yourself",
) -> bool:
    """Replace everything ``grantee_id`` waits for on ``resource_type`` in the
    pod with ``grants``; whether they are held (not before migration 0077).

    Replace, like ``replace_grantee_resource_grants``: what the writer lists
    is the whole set, so a grant the grantee no longer needs stops waiting.
    Only of the writer's own resource type: what another module holds for
    the same grantee is that module's. Each key's lock is taken, and kept
    to commit, before anything is written.
    """
    if any(grant.resource_type is not resource_type for grant in grants):
        raise ValueError(f"every pending grant here must be on {resource_type.value}")
    requested = {p for grant in grants for p in grant.permission_ids}
    if requested and ctx.is_named_workload:
        raise DomainError(
            "An agent or function may not change who can do what.",
            code=CONFERRAL_EXCEEDS_HOLDER,
            status_code=403,
        )
    assert_can_confer(ctx, requested, action=action)
    if not await _table_ready(session):
        logger.warning(
            "authorization.pending_grants.not_migrated.degraded",
            pod_id=str(pod_id),
            grantee_type=grantee_type,
        )
        return False
    # Every key the replace touches -- the ones it holds and the ones it
    # stops holding -- so a settlement of a dropped key cannot read a row
    # this deletes.
    await lock_grantee_pending_keys(
        session,
        pod_id=pod_id,
        resource_type=resource_type,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        also=[g.resource_key for g in grants],
    )
    await delete_grantee_pending_grants(
        session,
        pod_id=pod_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        resource_type=resource_type,
    )
    rows = {
        (grant.resource_type.value, grant.resource_key, permission_id)
        for grant in grants
        for permission_id in grant.permission_ids
    }
    await ensure_permission_rows(session, {row[2] for row in rows})
    for resource_type_value, resource_key, permission_id in sorted(rows):
        session.add(
            PendingResourceGrantModel(
                pod_id=pod_id,
                resource_type=resource_type_value,
                resource_key=resource_key,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                permission_id=permission_id,
                created_by_user_id=created_by_user_id,
            )
        )
    await session.flush()
    return True


async def forget_grantee_pending_grants(
    session: AsyncSession, pod_id: UUID, grantee_type: str, grantee_id: UUID
) -> None:
    """Forget everything ``grantee_id`` waits for, of every resource type:
    what it holds was just replaced whole, or it is gone. Each key is locked
    first, so a settlement racing this cannot grant from a row it deletes."""
    if not await _table_ready(session):
        return
    types = await session.execute(
        select(PendingResourceGrantModel.resource_type)
        .where(
            PendingResourceGrantModel.pod_id == pod_id,
            PendingResourceGrantModel.grantee_type == grantee_type,
            PendingResourceGrantModel.grantee_id == grantee_id,
        )
        .distinct()
        .order_by(PendingResourceGrantModel.resource_type)
        .limit(_SETTLE_BATCH)
    )
    for resource_type in types.scalars().all():
        await lock_grantee_pending_keys(
            session,
            pod_id=pod_id,
            resource_type=ResourceType(resource_type),
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )
    await delete_grantee_pending_grants(
        session, pod_id=pod_id, grantee_type=grantee_type, grantee_id=grantee_id
    )


async def delete_grantee_pending_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    resource_type: ResourceType | None = None,
) -> None:
    """Forget what ``grantee_id`` was waiting for in the pod: on
    ``resource_type``, or everything when the grantee itself is gone."""
    if not await _table_ready(session):
        return
    scope = [
        PendingResourceGrantModel.pod_id == pod_id,
        PendingResourceGrantModel.grantee_type == grantee_type,
        PendingResourceGrantModel.grantee_id == grantee_id,
    ]
    if resource_type is not None:
        scope.append(PendingResourceGrantModel.resource_type == resource_type.value)
    await session.execute(delete(PendingResourceGrantModel).where(*scope))


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
    if not await _table_ready(session):
        return 0
    await lock_pending_key(
        session, pod_id=pod_id, resource_type=resource_type, resource_key=resource_key
    )
    permissions: dict[tuple[str, UUID], set[str]] = {}
    granted_by: dict[tuple[str, UUID], UUID | None] = {}
    while batch := await _next_batch(session, pod_id, resource_type, resource_key):
        for _row_id, grantee_type, grantee_id, permission_id, created_by in batch:
            grantee = (grantee_type, grantee_id)
            permissions.setdefault(grantee, set()).add(permission_id)
            granted_by[grantee] = created_by
        await _delete_rows(session, (row[0] for row in batch))
        if len(batch) < _SETTLE_BATCH:
            break
    # Imported here: `grants` imports this module.
    from app.core.authorization.grants import replace_resource_grantee_grant

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
    return len(permissions)


async def _next_batch(
    session: AsyncSession, pod_id: UUID, resource_type: ResourceType, resource_key: str
) -> list[tuple[UUID, str, UUID, str, UUID | None]]:
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
        .limit(_SETTLE_BATCH)
    )
    return [(row[0], row[1], row[2], row[3], row[4]) for row in found.all()]


async def _delete_rows(session: AsyncSession, row_ids: Iterable[UUID]) -> None:
    await session.execute(
        delete(PendingResourceGrantModel).where(
            PendingResourceGrantModel.id.in_(list(row_ids))
        )
    )
    await session.flush()
