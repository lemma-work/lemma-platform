"""The `machine.use` grant an agent's `machine:` field implies.

Naming a pod machine as the one an agent's commands run on, and granting the
agent that machine, are one decision: an agent told to work on `pod:qa`
without the grant fails on its first command, and a grant nobody named is
the drift a reviewer cannot see. So the grant is derived from the field,
like the `/memory` grant is derived from the MEMORY toolset
(``agent_memory_grant``), and re-derived after anything that replaces the
agent's grants.

Conferral is bounded the usual way (``core/authorization/conferral``): the
person saving the agent must be able to use that machine themselves.

Adding only. Pointing the agent at another machine leaves the grant on the
old one -- a tool call may still name any machine the run may use, and a
grant someone set by hand is not this field's to take away. A ref that names
no machine of this pod yet (or `me`, or a connected machine) confers nothing;
the next save after the machine exists does.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.conferral import assert_can_confer_grants
from app.core.authorization.context import Context, ResourceType
from app.core.authorization.grants import (
    NormalizedResourceGrant,
    replace_resource_grantee_grant,
)
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


async def sync_agent_machine_grant(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    agent_id: UUID,
    machine: str | None,
    ctx: Context,
    created_by_user_id: UUID,
) -> None:
    if not machine:
        return
    from app.modules.workspace.contracts.pod_machines import pod_machine_id

    machine_id = await pod_machine_id(uow, pod_id=pod_id, ref=machine)
    if machine_id is None:
        return
    await assert_can_confer_grants(
        ctx,
        [
            NormalizedResourceGrant(
                resource_type=ResourceType.MACHINE,
                resource_id=machine_id,
                permission_ids=[Permissions.MACHINE_USE],
            )
        ],
        pod_id=pod_id,
        action="give an agent a machine you could not use yourself",
    )
    await replace_resource_grantee_grant(
        uow.session,
        pod_id=pod_id,
        resource_type=ResourceType.MACHINE,
        resource_id=machine_id,
        grantee_type="AGENT",
        grantee_id=agent_id,
        permission_ids=[Permissions.MACHINE_USE],
        created_by_user_id=created_by_user_id,
    )


__all__ = ["sync_agent_machine_grant"]
