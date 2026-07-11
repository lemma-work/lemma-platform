"""Read queries for pod role assignments (core authorization role tables)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.authorization.models import RoleAssignmentModel, RoleModel
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.composition.pod_identity_wiring import OrganizationMember
from app.modules.pod.domain.role_entities import PodRoleEntity
from app.modules.pod.infrastructure.models.pod_models import PodMember


class PodRoleQueryRepository:
    """SQLAlchemy reads behind ``PodRoleService`` so the service stays ORM-free."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    @staticmethod
    def _to_entity(role: RoleModel, *, pod_id: UUID) -> PodRoleEntity:
        return PodRoleEntity(
            id=role.id,
            pod_id=pod_id,
            name=role.name,
            is_system=role.is_system,
            created_by_user_id=role.created_by_user_id,
            created_at=role.created_at,
        )

    async def get_roles_by_names(
        self, *, pod_id: UUID, names: list[str]
    ) -> list[PodRoleEntity]:
        if not names:
            return []
        stmt = select(RoleModel).where(
            RoleModel.pod_id == pod_id,
            RoleModel.name.in_(names),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_entity(role, pod_id=pod_id) for role in rows]

    async def get_member_role_names(
        self,
        *,
        pod_id: UUID,
        organization_id: UUID,
        user_id: UUID,
    ) -> list[str]:
        stmt = (
            select(RoleModel.name)
            .join(RoleAssignmentModel, RoleAssignmentModel.role_id == RoleModel.id)
            .join(PodMember, PodMember.id == RoleAssignmentModel.principal_id)
            .join(
                OrganizationMember,
                PodMember.organization_member_id == OrganizationMember.id,
            )
            .where(
                RoleModel.organization_id == organization_id,
                RoleModel.pod_id == pod_id,
                RoleAssignmentModel.principal_type == "POD_MEMBER",
                PodMember.pod_id == pod_id,
                OrganizationMember.user_id == user_id,
            )
            .order_by(RoleModel.name)
        )
        return list((await self._session.execute(stmt)).scalars().all())
