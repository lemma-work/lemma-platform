"""Read queries for pod role assignments (core authorization role tables)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.authorization.models import (
    RoleAssignmentModel,
    RoleModel,
    RolePermissionModel,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.contracts.orm import OrganizationMember
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

    async def get_permission_ids_by_role_name(
        self, *, pod_id: UUID, names: list[str]
    ) -> dict[str, set[str]]:
        """Permission ids carried by each named pod role.

        Roles the pod does not define are absent from the result rather than
        mapped to an empty set, so a caller can tell "this role grants nothing"
        apart from "there is no such role".
        """
        if not names:
            return {}
        stmt = (
            select(RoleModel.name, RolePermissionModel.permission_id)
            .join(
                RolePermissionModel,
                RolePermissionModel.role_id == RoleModel.id,
                isouter=True,
            )
            .where(
                RoleModel.pod_id == pod_id,
                RoleModel.name.in_(names),
            )
        )
        permissions: dict[str, set[str]] = {}
        for role_name, permission_id in (await self._session.execute(stmt)).all():
            carried = permissions.setdefault(role_name, set())
            if permission_id is not None:
                carried.add(permission_id)
        return permissions

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
