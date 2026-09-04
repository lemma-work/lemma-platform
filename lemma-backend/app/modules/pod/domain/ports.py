"""Pod module ports (repository and external dependencies)."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, Tuple
from uuid import UUID

from app.modules.identity.contracts import (
    OrganizationEntity,
    OrganizationMemberEntity,
)
from app.modules.pod.domain.pod_entities import (
    PodEntity,
    PodJoinRequestEntity,
    PodJoinRequestStatus,
    PodMemberEntity,
)


class PodRepositoryPort(Protocol):
    async def create(self, entity: PodEntity) -> PodEntity: ...

    async def get(self, id: UUID) -> Optional[PodEntity]: ...

    #: The pod row whether or not it has been deleted. Only deletion itself
    #: wants this: every other read means the live pod, which is why `get`
    #: filters and this is the exception that has to say so in its name.
    async def get_even_if_deleted(self, id: UUID) -> Optional[PodEntity]: ...

    async def update(self, entity: PodEntity) -> PodEntity: ...

    async def delete(self, id: UUID) -> bool: ...

    async def list_by_org(
        self, organization_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[PodEntity], Optional[str]]: ...

    async def list_by_org_member(
        self,
        organization_id: UUID,
        organization_member_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[PodEntity], Optional[str]]: ...


class PodMemberRepositoryPort(Protocol):
    async def create(self, entity: PodMemberEntity) -> PodMemberEntity: ...

    async def get(self, id: UUID) -> Optional[PodMemberEntity]: ...

    async def get_by_pod_and_id(
        self, pod_id: UUID, pod_member_id: UUID
    ) -> Optional[PodMemberEntity]: ...

    async def update(self, entity: PodMemberEntity) -> PodMemberEntity: ...

    async def delete(self, id: UUID) -> bool: ...

    async def delete_entity(self, entity: PodMemberEntity) -> bool: ...

    async def get_by_pod_and_org_member(
        self, pod_id: UUID, org_member_id: UUID
    ) -> Optional[PodMemberEntity]: ...

    async def get_by_pod_and_user_id(
        self, pod_id: UUID, user_id: UUID
    ) -> Optional[PodMemberEntity]: ...

    async def get_by_pod_and_user_email(
        self, pod_id: UUID, email: str
    ) -> Optional[PodMemberEntity]: ...

    async def list_pod_members(
        self, pod_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[PodMemberEntity], Optional[str]]: ...

    async def check_user_has_pod_access(
        self, pod_id: UUID, org_member_id: UUID
    ) -> bool: ...

    async def count_members_who_can(self, pod_id: UUID, permission_id: str) -> int: ...

    async def roles_grant_permission(
        self, pod_id: UUID, role_names: Sequence[str], permission_id: str
    ) -> bool: ...


class PodScheduleTeardownPort(Protocol):
    """What pod deletion needs from the schedule module, stated locally.

    Deleting a pod must stop its standing work in the same request -- an
    event-driven cleanup is a retry away from a deleted pod firing agents
    nobody can see. Stopping it is all the request does: the rows and the
    provider triggers behind them are torn down on the pod-deleted event, which
    needs those rows to know what to tear down. See PS-OPS-020 and DEV-OPS-003.
    """

    async def disarm_all_for_pod(self, pod_id: UUID) -> int: ...


class PodJoinRequestRepositoryPort(Protocol):
    async def create(self, entity: PodJoinRequestEntity) -> PodJoinRequestEntity: ...

    async def update(self, entity: PodJoinRequestEntity) -> PodJoinRequestEntity: ...

    async def get(self, id: UUID) -> Optional[PodJoinRequestEntity]: ...

    async def get_pending_by_pod_and_user(
        self, pod_id: UUID, user_id: UUID
    ) -> Optional[PodJoinRequestEntity]: ...

    async def list_by_pod(
        self,
        pod_id: UUID,
        *,
        status: PodJoinRequestStatus | None = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[PodJoinRequestEntity], Optional[str]]: ...


class OrganizationMembershipPort(Protocol):
    #: The organization itself, for the one pod decision whose effect crosses
    #: the organization boundary: a pod may not open itself wider than the
    #: organization that holds it (see ``PodService.update_pod``).
    async def get(self, organization_id: UUID) -> Optional[OrganizationEntity]: ...

    async def get_member(
        self, user_id: UUID, organization_id: UUID
    ) -> Optional[OrganizationMemberEntity]: ...

    async def get_member_by_id(
        self, member_id: UUID
    ) -> Optional[OrganizationMemberEntity]: ...

    async def add_member(
        self, entity: OrganizationMemberEntity
    ) -> OrganizationMemberEntity: ...
