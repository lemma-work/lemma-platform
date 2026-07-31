"""Pod role and member role management backed by core authorization roles."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.authorization.cache import invalidate_role_snapshot_cache
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.grants import delete_grantee_grants
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod.domain.role_entities import PodRoleEntity, SYSTEM_ROLE_NAMES
from app.modules.pod.domain.roles import PodRole
from app.modules.pod.domain.visibility import (
    ROLE_HIERARCHY,
    normalize_role_list,
    normalize_role_name,
    roles_allow_required,
)
from app.modules.pod.infrastructure.pod_repositories import PodRepository
from app.modules.pod.infrastructure.pod_role_repository import PodRoleQueryRepository


class PodRoleService:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self._authz = create_authorization_data_service(uow)
        self._pods = PodRepository(uow)
        self._roles = PodRoleQueryRepository(uow)

    async def ensure_system_roles(
        self,
        *,
        pod_id: UUID,
        created_by_user_id: UUID | None = None,
    ) -> dict[str, PodRoleEntity]:
        _ = created_by_user_id
        pod_org_id = await self._pods.get_organization_id(pod_id)
        if pod_org_id is None:
            return {}
        await self._authz.ensure_pod_system_roles(
            organization_id=pod_org_id,
            pod_id=pod_id,
        )
        rows = await self._roles.get_roles_by_names(
            pod_id=pod_id,
            names=[role.value for role in PodRole],
        )
        return {role.name: role for role in rows}

    async def create_role(
        self,
        *,
        pod_id: UUID,
        name: str,
        created_by_user_id: UUID,
    ) -> PodRoleEntity:
        role_name = normalize_role_name(name)
        if role_name in SYSTEM_ROLE_NAMES:
            raise HTTPException(status_code=400, detail="System role name is reserved")
        pod_org_id = await self._pods.get_organization_id(pod_id)
        if pod_org_id is None:
            raise HTTPException(status_code=404, detail="Pod not found")
        role = await self._authz.create_or_update_role(
            organization_id=pod_org_id,
            pod_id=pod_id,
            name=role_name,
            permission_ids=[],
            created_by_user_id=created_by_user_id,
        )
        return PodRoleEntity(
            id=role.id,
            pod_id=pod_id,
            name=role.name,
            is_system=role.is_system,
            created_by_user_id=role.created_by_user_id,
            created_at=role.created_at,
        )

    async def delete_role(self, *, pod_id: UUID, role_name: str) -> None:
        pod_org_id = await self._pods.get_organization_id(pod_id)
        if pod_org_id is None:
            raise HTTPException(status_code=404, detail="Pod not found")
        try:
            await self._authz.delete_role(
                organization_id=pod_org_id,
                pod_id=pod_id,
                name=role_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def list_roles(self, *, pod_id: UUID) -> list[PodRoleEntity]:
        pod_org_id = await self._pods.get_organization_id(pod_id)
        if pod_org_id is None:
            return []
        roles = await self._authz.list_roles(
            organization_id=pod_org_id,
            pod_id=pod_id,
        )
        return [
            PodRoleEntity(
                id=role.id,
                pod_id=pod_id,
                name=role.name,
                is_system=role.is_system,
                created_by_user_id=role.created_by_user_id,
                created_at=role.created_at,
            )
            for role in roles
        ]

    async def sync_member_roles(
        self,
        *,
        pod_id: UUID,
        pod_member_id: UUID,
        roles: list[str | PodRole],
        added_by_user_id: UUID | None,
    ) -> list[str]:
        pod_org_id = await self._pods.get_organization_id(pod_id)
        if pod_org_id is None:
            raise HTTPException(status_code=404, detail="Pod not found")
        await self._authz.ensure_pod_system_roles(
            organization_id=pod_org_id,
            pod_id=pod_id,
        )
        normalized_roles = normalize_role_list(roles)
        if not normalized_roles:
            raise HTTPException(status_code=400, detail="At least one role is required")

        role_rows = await self._roles.get_roles_by_names(pod_id=pod_id, names=normalized_roles)
        missing = set(normalized_roles) - {role.name for role in role_rows}
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown pod role(s): {', '.join(sorted(missing))}",
            )

        await self._authz.assign_roles(
            organization_id=pod_org_id,
            pod_id=pod_id,
            principal_type="POD_MEMBER",
            principal_id=pod_member_id,
            role_names=normalized_roles,
            assigned_by_user_id=added_by_user_id,
        )
        return normalized_roles

    async def revoke_member_authorization(
        self,
        *,
        pod_id: UUID,
        pod_member_id: UUID,
        user_id: UUID | None,
    ) -> None:
        """Drop a removed pod member's role assignments and resource grants and
        invalidate their cached role snapshot, so pod access is revoked on the
        next request instead of lingering until the snapshot TTL elapses."""
        await self._authz.delete_principal_role_assignments(
            principal_type="POD_MEMBER", principal_id=pod_member_id
        )
        await delete_grantee_grants(
            self._authz.session,
            pod_id=pod_id,
            grantee_type="POD_MEMBER",
            grantee_id=pod_member_id,
        )
        await invalidate_role_snapshot_cache(user_id=user_id)

    async def get_member_roles_by_user_id(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
    ) -> list[str]:
        pod_org_id = await self._pods.get_organization_id(pod_id)
        if pod_org_id is None:
            return []
        names = await self._roles.get_member_role_names(
            pod_id=pod_id,
            organization_id=pod_org_id,
            user_id=user_id,
        )
        return normalize_role_list(names)

    async def require_role_manager_bounds(
        self,
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        target_roles: list[str | PodRole],
        target_user_id: UUID | None = None,
    ) -> None:
        requester_roles = await self.get_member_roles_by_user_id(
            pod_id=pod_id,
            user_id=requester_user_id,
        )
        if roles_allow_required(requester_roles, PodRole.ADMIN):
            return
        if not roles_allow_required(requester_roles, PodRole.EDITOR):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Pod editor or admin role is required",
            )
        if target_user_id == requester_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Editors cannot change their own pod roles",
            )
        normalized_target_roles = normalize_role_list(target_roles)
        if any(
            ROLE_HIERARCHY.get(role, 0) > ROLE_HIERARCHY[PodRole.EDITOR.value]
            for role in normalized_target_roles
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Editors cannot assign roles above POD_EDITOR",
            )
