"""The pod a surface set-up is for, read again after the permission check."""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.errors import AgentSurfacePodNotFoundError
from app.modules.pod.contracts.agent_access import pod_organization_id
from app.modules.pod.contracts.members import pod_name


async def pod_scope(uow: SqlAlchemyUnitOfWork, pod_id: UUID) -> tuple[UUID, str]:
    """The pod's organization and name, which a surface set-up records.

    Raises the surface module's not-found error: a pod deleted between the
    permission check and this read answers 404, where a bare `ValueError`
    answered 500.
    """
    organization_id = await pod_organization_id(uow, pod_id)
    name = await pod_name(uow.session, pod_id)
    if organization_id is None or name is None:
        raise AgentSurfacePodNotFoundError(str(pod_id))
    return organization_id, name
