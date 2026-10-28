"""Whether a grant's grantee is one of this pod's own.

A grant to a role or a member names it by id, and the grant is stored with the
pod's id beside it -- so an id from another pod is accepted, matches nobody, and
stays behind as a dead row. Every path that writes a ROLE or POD_MEMBER grant
asks here first: resource access, and an owner sharing their account with some
people (`connectors`).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.models import RoleModel
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember


async def require_pod_grantee(
    session: AsyncSession, *, pod_id: UUID, grantee_type: str, grantee_id: UUID
) -> None:
    """Refuse a ROLE or POD_MEMBER grantee that is not in ``pod_id``.

    404s, as for any other id the caller cannot find in the pod.
    """
    if grantee_type == "ROLE":
        pod = await session.get(Pod, pod_id)
        if pod is None:
            raise DomainError("Pod not found", code="POD_NOT_FOUND", status_code=404)
        # The system roles are created on first use, so a pod that has never
        # been asked about its roles still has them by the time we look.
        await AuthorizationDataService(session).ensure_pod_system_roles(
            organization_id=pod.organization_id, pod_id=pod_id
        )
        found = await session.scalar(
            select(RoleModel.id).where(
                RoleModel.pod_id == pod_id, RoleModel.id == grantee_id
            )
        )
        if found is None:
            raise DomainError("Role not found", code="ROLE_NOT_FOUND", status_code=404)
        return
    if grantee_type == "POD_MEMBER":
        found = await session.scalar(
            select(PodMember.id).where(
                PodMember.pod_id == pod_id, PodMember.id == grantee_id
            )
        )
        if found is None:
            raise DomainError(
                "Pod member not found", code="POD_MEMBER_NOT_FOUND", status_code=404
            )
        return
    raise DomainError(
        f"A grant cannot name a {grantee_type} here.",
        code="UNSUPPORTED_GRANTEE_TYPE",
        status_code=422,
    )
