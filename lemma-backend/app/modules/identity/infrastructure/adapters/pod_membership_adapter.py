"""Identity's :class:`PodMembershipPort`, over pod's published membership operations.

An organization invitation can name a pod, and accepting it has to put the
invitee in that pod. Identity declares what it needs as a port; this is the
adapter that satisfies it.

It was `app/composition/pod_identity.py`, where it built `PodRepository`,
`PodMemberRepository` and `PodRoleService` and drove all three -- so a file in
neither module knew that adding a member is a row *and* a role sync, and that an
unrecognised role name means `USER`. Those are pod's rules and pod states them
now; what is left here is three calls and the port's shape.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.ports import PodMembershipPort
from app.modules.pod.contracts.members import (
    add_pod_member,
    pod_invitation_details,
    pod_organization_id,
)


class SqlAlchemyPodMembershipAdapter(PodMembershipPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        return await pod_organization_id(self._uow, pod_id)

    async def get_pod_invitation_details(
        self, pod_id: UUID
    ) -> tuple[str, str | None, UUID] | None:
        return await pod_invitation_details(self._uow, pod_id)

    async def add_member_to_pod(
        self,
        *,
        pod_id: UUID,
        organization_member_id: UUID,
        user_id: UUID,
        user_email: str,
        user_name: str | None,
        pod_role: str,
    ) -> None:
        await add_pod_member(
            self._uow,
            pod_id=pod_id,
            organization_member_id=organization_member_id,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
            pod_role=pod_role,
        )
