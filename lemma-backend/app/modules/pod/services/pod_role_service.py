"""Pod role and member role management backed by core authorization roles."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.authorization.cache import invalidate_role_snapshot_cache
from app.core.authorization.conferral import refuse_conferral_beyond
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.grants import delete_grantee_grants
from app.core.authorization.permissions import (
    equivalent_permission_ids,
    Permissions,
    SYSTEM_ROLE_PERMISSIONS,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod.domain.role_entities import PodRoleEntity, SYSTEM_ROLE_NAMES
from app.modules.pod.domain.roles import PodRole
from app.modules.pod.domain.visibility import (
    normalize_role_list,
    normalize_role_name,
)
from app.modules.pod.infrastructure.pod_repositories import PodRepository
from app.modules.pod.infrastructure.pod_role_repository import PodRoleQueryRepository


class PodRoleService:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self._uow = uow
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

        role_rows = await self._roles.get_roles_by_names(
            pod_id=pod_id, names=normalized_roles
        )
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
        next request instead of lingering until the snapshot TTL elapses.

        The invalidation runs *after* the commit, and that is a correctness
        point rather than a connection one. Invalidating inline is the wrong
        order: between the delete and the caller's commit, a concurrent request
        for this user can miss the cache, rebuild the snapshot from rows that
        still grant access, and store it again -- leaving the removed member
        with pod access until the TTL elapses, which is the exact outcome this
        method exists to prevent. It also held a pooled connection across the
        Redis round trip, which is how it was found.
        """
        await self._authz.delete_principal_role_assignments(
            principal_type="POD_MEMBER", principal_id=pod_member_id
        )
        await delete_grantee_grants(
            self._authz.session,
            pod_id=pod_id,
            grantee_type="POD_MEMBER",
            grantee_id=pod_member_id,
        )
        self._uow.after_commit(lambda: invalidate_role_snapshot_cache(user_id=user_id))

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
        target_current_roles: list[str | PodRole] | None = None,
        requester_is_org_owner: bool = False,
    ) -> None:
        """Refuse a membership change that reaches beyond what the requester holds.

        Three questions, all put to *permissions* and none to role names:

        1. May the requester manage this pod's members at all? They hold
           ``pod.member.manage``, or they are an organization owner.
        2. Does everything ``target_roles`` carries sit inside what the
           requester holds? Nobody confers what they lack (PS-POD-013).
        3. Does everything the member holds *today* sit inside it too? Removing
           or demoting somebody is the mirror of granting them the role: a
           custom "member manager" must not be able to remove the administrator.

        The bound used to be a rank comparison against ``ROLE_HIERARCHY``, which
        names only the four built-in roles -- so ``.get(role, 0)`` scored every
        *custom* role zero and waved it through. A custom role carrying
        ``pod.member.manage`` was, to that check, indistinguishable from
        POD_VIEWER, and assigning it made the target an administrator in
        everything but name. The same rank test then sat *in front of* the set
        comparison as a gate, and answered the first question with role names:
        an organization owner holding no pod role scored zero and was refused
        the very pod they own, and so was anyone whose only administrative role
        was custom -- while the request had already been admitted by the
        ``pod.member.manage`` dependency that says both may.

        An organization owner is exempt from 2 and 3: they already hold every
        authority the organization can express, so there is nothing for the
        bound to protect -- the same exemption ``assert_can_confer`` makes.
        """
        if requester_is_org_owner:
            return
        requester_roles = await self.get_member_roles_by_user_id(
            pod_id=pod_id,
            user_id=requester_user_id,
        )
        target = normalize_role_list(target_roles)
        current = normalize_role_list(target_current_roles or [])
        carried = await self._permission_ids_by_role_name(
            pod_id=pod_id,
            role_names=sorted({*requester_roles, *target, *current}),
        )
        held: set[str] = set()
        for role in requester_roles:
            held |= carried.get(role, set())
        if not (equivalent_permission_ids(Permissions.POD_MEMBER_MANAGE) & held):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You may not manage this pod's members",
            )
        # Raised as a DomainError and left to propagate: the global handler
        # translates it with its own code intact, where an HTTPException would
        # flatten the refusal to a bare HTTP_403 the client cannot branch on.
        refuse_conferral_beyond(
            held=held,
            requested=set().union(*(carried.get(role, set()) for role in target)),
            action="assign a role carrying permissions you do not hold",
        )
        refuse_conferral_beyond(
            held=held,
            requested=set().union(*(carried.get(role, set()) for role in current)),
            action=(
                "change the roles of, or remove, a member who holds "
                "permissions you do not"
            ),
        )

    async def _permission_ids_by_role_name(
        self,
        *,
        pod_id: UUID,
        role_names: list[str],
    ) -> dict[str, set[str]]:
        """Permission ids per role name, with built-in roles resolved from code.

        The rows are authoritative for custom roles. Built-in roles are seeded
        from ``SYSTEM_ROLE_PERMISSIONS`` and are re-seeded on demand, so a pod
        whose system rows have not been written yet would otherwise resolve
        POD_ADMIN to the empty set -- and an empty target set passes any bound.
        Reading them from the constant makes the check independent of whether
        the seeding has happened.
        """
        stored = await self._roles.get_permission_ids_by_role_name(
            pod_id=pod_id,
            names=role_names,
        )
        return {
            name: set(SYSTEM_ROLE_PERMISSIONS.get(name, ())) | stored.get(name, set())
            for name in role_names
        }
