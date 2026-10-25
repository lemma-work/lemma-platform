"""Grants on pod connectors and pod accounts, removed with the rows.

``resource_permission_grants.resource_id`` has no foreign key, so when a pod
account goes with its org account, or a pod connector with its installation,
the database cascade removes the row and leaves its grants pointing at nothing.
A dangling ``connector_account.use`` grant is harmless until an id is reused;
a dangling ``connector.use`` grant still shows up as a tool an agent holds.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.authz_epoch import bump_after_commit
from app.core.authorization.context import ResourceType
from app.core.authorization.grant_generation import (
    AGENT_GRANTEE,
    bump_agent_grant_generations,
)
from app.core.authorization.grants import grant_resource_type_values
from app.core.authorization.models import ResourcePermissionGrantModel


async def delete_grants_on(
    session: AsyncSession,
    *,
    resource_type: ResourceType,
    resource_ids: Sequence[UUID],
) -> None:
    """Delete every grant on these resources, in whichever pods they are."""
    if not resource_ids:
        return
    deleted = await session.execute(
        delete(ResourcePermissionGrantModel)
        .where(
            ResourcePermissionGrantModel.resource_type.in_(
                grant_resource_type_values(resource_type)
            ),
            ResourcePermissionGrantModel.resource_id.in_(list(resource_ids)),
        )
        .returning(
            ResourcePermissionGrantModel.pod_id,
            ResourcePermissionGrantModel.grantee_type,
            ResourcePermissionGrantModel.grantee_id,
        )
    )
    rows = deleted.all()
    # Every agent that held one has just lost a tool.
    await bump_agent_grant_generations(
        session,
        {grantee for _, kind, grantee in rows if kind == AGENT_GRANTEE},
    )
    await session.flush()
    for pod_id in {pod_id for pod_id, _, _ in rows}:
        bump_after_commit(session, pod_id)
