"""The ``auth_permissions`` rows, written where something is about to need them.

Grant and role-permission rows carry a foreign key to them, and nothing seeds
them at boot (boot hooks must not grow with data, and this needs none). They
are written lazily instead: every definition when a scope's system roles are
first ensured (``AuthorizationDataService.seed_permissions``), and the ones a
grant names just before the grant is written. So a permission added in a
release -- ``channel.notify_self`` -- can be granted on a pod nobody has asked
about since, without a migration having to run first.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.models import AuthPermissionModel
from app.core.authorization.permissions import PERMISSION_BY_ID


async def ensure_permission_rows(
    session: AsyncSession, permission_ids: Collection[str]
) -> bool:
    """Write the rows for ``permission_ids``; whether any was new.

    Only known definitions are written: validation has refused an unknown id
    before a writer is reached. One upsert, not check-then-insert, so two
    writers at once cannot both decide a row is missing and one of them lose
    to the primary key.
    """
    rows = [
        {
            "id": definition.id,
            "scope": definition.scope.value,
            "resource_type": definition.resource_type,
            "description": definition.description,
            "system_only": definition.system_only,
        }
        for permission_id in sorted(permission_ids)
        if (definition := PERMISSION_BY_ID.get(permission_id)) is not None
    ]
    if not rows:
        return False
    result = await session.execute(
        insert(AuthPermissionModel)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    return result.rowcount > 0


__all__ = ["ensure_permission_rows"]
