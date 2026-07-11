"""Bind identity's pod-membership port to the pod module."""

from uuid import UUID

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.ports import PodMembershipPort
from app.modules.pod.domain.pod_entities import PodMemberEntity
from app.modules.pod.domain.roles import PodRole
from app.modules.pod.infrastructure.pod_repositories import (
    PodMemberRepository,
    PodRepository,
)
from app.modules.pod.services.pod_role_service import PodRoleService


class SqlAlchemyPodMembershipAdapter(PodMembershipPort):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ) -> None:
        self._pod_repository = PodRepository(uow, message_bus=message_bus)
        self._member_repository = PodMemberRepository(uow, message_bus=message_bus)
        self._role_service = PodRoleService(uow)

    async def get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        pod = await self._pod_repository.get(pod_id)
        return pod.organization_id if pod is not None else None

    async def get_pod_invitation_details(
        self, pod_id: UUID
    ) -> tuple[str, str | None, UUID] | None:
        pod = await self._pod_repository.get(pod_id)
        if pod is None:
            return None
        return pod.name, pod.description, pod.organization_id

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
        try:
            resolved_role = PodRole(pod_role)
        except ValueError:
            resolved_role = PodRole.USER
        entity = PodMemberEntity(
            pod_id=pod_id,
            organization_member_id=organization_member_id,
            roles=[resolved_role.value],
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
        )
        names = user_name.split(" ", 1) if user_name else []
        entity.mark_added(
            user_id=user_id,
            email=user_email,
            first_name=names[0] if names else None,
            last_name=names[1] if len(names) > 1 else None,
        )
        member = await self._member_repository.create(entity)
        await self._role_service.sync_member_roles(
            pod_id=pod_id,
            pod_member_id=member.id,
            roles=[resolved_role],
            added_by_user_id=None,
        )
