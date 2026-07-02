"""Authorization data service and authorizer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import (
    ActorType,
    AuthorizationDecision,
    Context,
    PrincipalRef,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.cache import (
    RoleSnapshot,
    get_role_snapshot,
    invalidate_role_snapshot_cache,
    set_role_snapshot,
)
from app.core.authorization.delegation import (
    DESTRUCTIVE_ACTIONS,
    DelegationClaims,
    WorkloadPrincipalType,
)
from app.core.authorization.session_approvals import has_session_approval
from app.core.authorization.grants import (
    delete_grantee_grants,
    grant_resource_type_values,
)
from app.core.authorization.models import (
    AuthPermissionModel,
    ResourcePermissionGrantModel,
    RoleAssignmentModel,
    RoleModel,
    RolePermissionModel,
)
from app.core.authorization.permissions import (
    PERMISSION_BY_ID,
    PERMISSION_DEFINITIONS,
    SYSTEM_ROLE_PERMISSIONS,
    PermissionScope,
    Permissions,
    equivalent_permission_ids,
)
from app.core.authorization.resource_actions import owner_actions_for_resource
from app.core.authorization.resource_names import resolve_resource_id_by_name
from app.modules.datastore.infrastructure.models.datastore_models import (
    DatastoreFile,
    DatastoreTable,
)
from app.modules.agent.infrastructure.models import AgentModel, ConversationModel
from app.modules.apps.infrastructure.models import AppModel
from app.modules.function.infrastructure.models import FunctionModel
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.pod.domain.visibility import (
    normalize_role_list,
    normalize_role_name,
)
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.workflow.infrastructure.models import FlowModel


SYSTEM_ORG_ROLES = {"ORG_MEMBER", "ORG_EDITOR", "ORG_OWNER"}
SYSTEM_POD_ROLES = {"POD_VIEWER", "POD_USER", "POD_EDITOR", "POD_ADMIN"}

# Scopes whose system roles are known to be fully provisioned. Entries are only
# added when an ensure pass found nothing to write, so a rolled-back transaction
# can never mark a scope as provisioned.
_ENSURED_ROLE_SCOPES: set[tuple[UUID, UUID | None]] = set()


@dataclass(frozen=True, slots=True)
class RoleSummary:
    id: UUID
    organization_id: UUID
    pod_id: UUID | None
    name: str
    description: str | None
    is_system: bool
    permission_ids: tuple[str, ...]
    created_by_user_id: UUID | None
    created_at: datetime


class AuthorizationDataService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def seed_permissions(self) -> bool:
        existing = set((await self.session.execute(select(AuthPermissionModel.id))).scalars())
        changed = False
        for definition in PERMISSION_DEFINITIONS:
            if definition.id in existing:
                continue
            changed = True
            self.session.add(
                AuthPermissionModel(
                    id=definition.id,
                    scope=definition.scope.value,
                    resource_type=definition.resource_type,
                    description=definition.description,
                    system_only=definition.system_only,
                )
            )
        if changed:
            await self.session.flush()
        return changed

    async def ensure_org_system_roles(self, organization_id: UUID) -> None:
        await self._ensure_system_roles(
            organization_id=organization_id,
            pod_id=None,
            role_names=SYSTEM_ORG_ROLES,
        )

    async def ensure_pod_system_roles(self, *, organization_id: UUID, pod_id: UUID) -> None:
        await self._ensure_system_roles(
            organization_id=organization_id,
            pod_id=pod_id,
            role_names=SYSTEM_POD_ROLES,
        )

    async def _ensure_system_roles(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID | None,
        role_names: set[str],
    ) -> None:
        scope = (organization_id, pod_id)
        if scope in _ENSURED_ROLE_SCOPES:
            return
        changed = await self.seed_permissions()
        for role_name in sorted(role_names):
            changed |= await self._ensure_role_with_permissions(
                organization_id=organization_id,
                pod_id=pod_id,
                name=role_name,
                permission_ids=SYSTEM_ROLE_PERMISSIONS[role_name],
                is_system=True,
                created_by_user_id=None,
            )
        if not changed:
            _ENSURED_ROLE_SCOPES.add(scope)

    async def list_roles(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID | None,
    ) -> list[RoleSummary]:
        if pod_id is None:
            await self.ensure_org_system_roles(organization_id)
        else:
            await self.ensure_pod_system_roles(
                organization_id=organization_id,
                pod_id=pod_id,
            )
        stmt = (
            select(RoleModel)
            .where(RoleModel.organization_id == organization_id, RoleModel.pod_id == pod_id)
            .order_by(RoleModel.is_system.desc(), RoleModel.name)
        )
        roles = list((await self.session.execute(stmt)).scalars().all())
        return [await self._to_summary(role) for role in roles]

    async def create_or_update_role(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID | None,
        name: str,
        permission_ids: list[str],
        description: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> RoleSummary:
        role_name = normalize_role_name(name)
        if role_name in SYSTEM_ROLE_PERMISSIONS:
            raise ValueError("System role name is reserved")
        unknown = set(permission_ids) - set(PERMISSION_BY_ID)
        if unknown:
            raise ValueError(f"Unknown permission id(s): {', '.join(sorted(unknown))}")
        role = await self._get_role(
            organization_id=organization_id,
            pod_id=pod_id,
            name=role_name,
        )
        if role is None:
            role = RoleModel(
                organization_id=organization_id,
                pod_id=pod_id,
                name=role_name,
                description=description,
                is_system=False,
                created_by_user_id=created_by_user_id,
            )
            self.session.add(role)
            await self.session.flush()
        else:
            role.description = description
        await self._replace_role_permissions(
            role_id=role.id,
            permission_ids=permission_ids,
            granted_by_user_id=created_by_user_id,
        )
        await self.session.flush()
        await invalidate_role_snapshot_cache(
            organization_id=organization_id, pod_id=pod_id
        )
        return await self._to_summary(role)

    async def delete_role(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID | None,
        name: str,
    ) -> None:
        role = await self._get_role(
            organization_id=organization_id,
            pod_id=pod_id,
            name=normalize_role_name(name),
        )
        if role is None:
            return
        if role.is_system:
            raise ValueError("System roles cannot be deleted")
        if role.pod_id is not None:
            await delete_grantee_grants(
                self.session,
                pod_id=role.pod_id,
                grantee_type="ROLE",
                grantee_id=role.id,
            )
        await self.session.delete(role)
        await self.session.flush()
        await invalidate_role_snapshot_cache(
            organization_id=organization_id, pod_id=pod_id
        )

    async def resolve_resource_id_by_name(
        self,
        *,
        resource_type: ResourceType,
        pod_id: UUID,
        resource_name: str,
    ) -> UUID | None:
        return await resolve_resource_id_by_name(
            self.session,
            pod_id=pod_id,
            resource_type=resource_type,
            resource_name=resource_name,
        )

    async def resolve_resource_ref(
        self,
        *,
        resource_type: ResourceType,
        pod_id: UUID,
        resource_id: UUID | None = None,
        resource_name: str | None = None,
    ) -> ResourceRef | None:
        resolved_resource_id = resource_id
        if resolved_resource_id is None and resource_name is not None:
            resolved_resource_id = await self.resolve_resource_id_by_name(
                resource_type=resource_type,
                pod_id=pod_id,
                resource_name=resource_name,
            )
        if resolved_resource_id is None:
            return None
        return ResourceRef(
            resource_type=resource_type,
            resource_id=resolved_resource_id,
            pod_id=pod_id,
        )

    async def get_resource_creator(
        self,
        *,
        resource_type: ResourceType,
        resource_id: UUID,
    ) -> UUID | None:
        if resource_type == ResourceType.AGENT:
            stmt = select(AgentModel.user_id).where(AgentModel.id == resource_id)
        elif resource_type == ResourceType.FUNCTION:
            stmt = select(FunctionModel.user_id).where(FunctionModel.id == resource_id)
        elif resource_type == ResourceType.APP:
            stmt = select(AppModel.user_id).where(AppModel.id == resource_id)
        elif resource_type == ResourceType.DOCUMENT:
            stmt = select(DatastoreFile.owner_user_id).where(DatastoreFile.id == resource_id)
        elif resource_type == ResourceType.FOLDER:
            stmt = select(DatastoreFile.owner_user_id).where(DatastoreFile.id == resource_id)
        elif resource_type == ResourceType.DATASTORE_TABLE:
            stmt = select(DatastoreTable.user_id).where(DatastoreTable.id == resource_id)
        elif resource_type == ResourceType.WORKFLOW:
            stmt = select(FlowModel.user_id).where(FlowModel.id == resource_id)
        elif resource_type == ResourceType.SCHEDULE:
            stmt = select(Schedule.user_id).where(Schedule.id == resource_id)
        else:
            return None
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def assign_roles(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID | None,
        principal_type: str,
        principal_id: UUID,
        role_names: list[str],
        assigned_by_user_id: UUID | None,
    ) -> list[str]:
        if pod_id is None:
            await self.ensure_org_system_roles(organization_id)
        else:
            await self.ensure_pod_system_roles(
                organization_id=organization_id,
                pod_id=pod_id,
            )
        normalized = normalize_role_list(role_names)
        roles = await self._get_roles_by_name(
            organization_id=organization_id,
            pod_id=pod_id,
            names=normalized,
        )
        missing = set(normalized) - {role.name for role in roles}
        if missing:
            raise ValueError(f"Unknown role(s): {', '.join(sorted(missing))}")
        await self.session.execute(
            delete(RoleAssignmentModel).where(
                RoleAssignmentModel.principal_type == principal_type,
                RoleAssignmentModel.principal_id == principal_id,
                RoleAssignmentModel.role_id.in_(select(RoleModel.id).where(
                    RoleModel.organization_id == organization_id,
                    RoleModel.pod_id == pod_id,
                )),
            )
        )
        for role in roles:
            self.session.add(
                RoleAssignmentModel(
                    role_id=role.id,
                    principal_type=principal_type,
                    principal_id=principal_id,
                    assigned_by_user_id=assigned_by_user_id,
                )
            )
        await self.session.flush()
        await invalidate_role_snapshot_cache(
            organization_id=organization_id, pod_id=pod_id
        )
        return normalized

    async def build_user_context(
        self,
        *,
        user_id: UUID,
        organization_id: UUID | None = None,
        pod_id: UUID | None = None,
        request_id: str | None = None,
    ) -> Context:
        authorizer = Authorizer(self.session)
        if organization_id is None and pod_id is None:
            return Context(
                actor_type=ActorType.USER,
                actor_id=str(user_id),
                user_id=user_id,
                authorizer=authorizer,
                request_id=request_id,
            )

        if pod_id is not None:
            pod = await self.session.get(Pod, pod_id)
            if pod is not None:
                organization_id = pod.organization_id

        cached = await get_role_snapshot(
            user_id=user_id,
            organization_id=organization_id,
            pod_id=pod_id,
        )
        if cached is not None:
            return Context(
                actor_type=ActorType.USER,
                actor_id=str(user_id),
                user_id=user_id,
                organization_id=cached.organization_id,
                pod_id=cached.pod_id,
                role_ids=cached.role_ids,
                role_names=cached.role_names,
                permission_ids=cached.permission_ids,
                principal_refs=cached.principal_refs,
                grant_principal_sets=cached.grant_principal_sets,
                authorizer=authorizer,
                request_id=request_id,
            )

        role_ids: set[UUID] = set()
        role_names: set[str] = set()
        permission_ids: set[str] = set()
        principal_refs: set[PrincipalRef] = set()

        if organization_id is not None:
            org_member = await self._get_org_member(
                user_id=user_id,
                organization_id=organization_id,
            )
            if org_member is not None:
                principal_refs.add(PrincipalRef("ORG_MEMBER", org_member.id))
                org_role_data = await self._load_principal_roles(
                    principal_type="ORG_MEMBER",
                    principal_id=org_member.id,
                    organization_id=organization_id,
                    pod_id=None,
                )
                self._merge_role_data(org_role_data, role_ids, role_names, permission_ids)

        if pod_id is not None and organization_id is not None:
            pod_member = await self._get_pod_member(user_id=user_id, pod_id=pod_id)
            if pod_member is not None:
                principal_refs.add(PrincipalRef("POD_MEMBER", pod_member.id))
                pod_role_data = await self._load_principal_roles(
                    principal_type="POD_MEMBER",
                    principal_id=pod_member.id,
                    organization_id=organization_id,
                    pod_id=pod_id,
                )
                self._merge_role_data(pod_role_data, role_ids, role_names, permission_ids)

        for role_id in role_ids:
            principal_refs.add(PrincipalRef("ROLE", role_id))

        snapshot = RoleSnapshot(
            organization_id=organization_id,
            pod_id=pod_id,
            role_ids=frozenset(role_ids),
            role_names=frozenset(role_names),
            permission_ids=frozenset(permission_ids),
            principal_refs=frozenset(principal_refs),
            grant_principal_sets=(frozenset(principal_refs),),
        )
        await set_role_snapshot(user_id=user_id, snapshot=snapshot)
        return Context(
            actor_type=ActorType.USER,
            actor_id=str(user_id),
            user_id=user_id,
            organization_id=snapshot.organization_id,
            pod_id=snapshot.pod_id,
            role_ids=snapshot.role_ids,
            role_names=snapshot.role_names,
            permission_ids=snapshot.permission_ids,
            principal_refs=snapshot.principal_refs,
            grant_principal_sets=snapshot.grant_principal_sets,
            authorizer=authorizer,
            request_id=request_id,
        )

    async def build_workload_context(
        self,
        *,
        principal_type: str,
        principal_id: UUID,
        pod_id: UUID,
        request_id: str | None = None,
    ) -> Context:
        authorizer = Authorizer(self.session)
        pod = await self.session.get(Pod, pod_id)
        organization_id = pod.organization_id if pod is not None else None
        normalized_principal_type = principal_type.upper()
        actor_type = ActorType.AGENT if normalized_principal_type == "AGENT" else ActorType.FUNCTION

        # The role snapshot cache is keyed by principal id; workload principals
        # (agent/function ids) share it with user ids without collision.
        cached = await get_role_snapshot(
            user_id=principal_id,
            organization_id=organization_id,
            pod_id=pod_id,
        )
        if cached is not None:
            return Context(
                actor_type=actor_type,
                actor_id=str(principal_id),
                organization_id=cached.organization_id,
                pod_id=cached.pod_id,
                role_ids=cached.role_ids,
                role_names=cached.role_names,
                permission_ids=cached.permission_ids,
                principal_refs=cached.principal_refs,
                grant_principal_sets=cached.grant_principal_sets,
                authorizer=authorizer,
                request_id=request_id,
            )

        role_ids: set[UUID] = set()
        role_names: set[str] = set()
        permission_ids: set[str] = set()
        principal_refs: set[PrincipalRef] = {
            PrincipalRef(normalized_principal_type, principal_id)
        }
        if organization_id is not None:
            await self.ensure_pod_system_roles(
                organization_id=organization_id,
                pod_id=pod_id,
            )
            role_data = await self._load_principal_roles(
                principal_type=normalized_principal_type,
                principal_id=principal_id,
                organization_id=organization_id,
                pod_id=pod_id,
            )
            self._merge_role_data(role_data, role_ids, role_names, permission_ids)
        for role_id in role_ids:
            principal_refs.add(PrincipalRef("ROLE", role_id))
        snapshot = RoleSnapshot(
            organization_id=organization_id,
            pod_id=pod_id,
            role_ids=frozenset(role_ids),
            role_names=frozenset(role_names),
            permission_ids=frozenset(permission_ids),
            principal_refs=frozenset(principal_refs),
            grant_principal_sets=(frozenset(principal_refs),),
        )
        await set_role_snapshot(user_id=principal_id, snapshot=snapshot)
        return Context(
            actor_type=actor_type,
            actor_id=str(principal_id),
            organization_id=organization_id,
            pod_id=pod_id,
            role_ids=snapshot.role_ids,
            role_names=snapshot.role_names,
            permission_ids=snapshot.permission_ids,
            principal_refs=snapshot.principal_refs,
            grant_principal_sets=snapshot.grant_principal_sets,
            authorizer=authorizer,
            request_id=request_id,
        )

    async def build_delegated_workload_context(
        self,
        *,
        user_id: UUID,
        principal_type: str,
        principal_id: UUID,
        pod_id: UUID,
        request_id: str | None = None,
        is_default_pod_agent: bool = False,
        delegation_scope: frozenset[str] | None = None,
        delegation_session_id: str | None = None,
        delegation_actor_name: str | None = None,
    ) -> Context:
        user_ctx = await self.build_user_context(
            user_id=user_id,
            pod_id=pod_id,
            request_id=request_id,
        )
        if is_default_pod_agent:
            user_ctx.actor_type = ActorType.DELEGATED_USER_WORKLOAD
            user_ctx.actor_id = f"{principal_type.lower()}:{principal_id}"
            user_ctx.delegated_by_user_id = user_id
            user_ctx.delegation_scope = delegation_scope or frozenset()
            user_ctx.delegation_session_id = delegation_session_id
            user_ctx.delegation_actor_name = delegation_actor_name
            # The default pod agent acts as the invoking user within its own pod
            # (org-wide actions go through gated approval tools, not this token).
            # Mark it user-equivalent so pod-scoped USER-only shortcuts apply —
            # otherwise an org-owner user whose pod permissions come only from the
            # org-owner shortcut loses them here (e.g. app.read -> spurious 403 on
            # `lemma pods import` / app deploys from the workspace).
            user_ctx.is_user_equivalent = True
            return user_ctx

        workload_ctx = await self.build_workload_context(
            principal_type=principal_type,
            principal_id=principal_id,
            pod_id=pod_id,
            request_id=request_id,
        )
        grant_principal_sets = (
            user_ctx.principal_refs,
            workload_ctx.principal_refs,
        )
        return Context(
            actor_type=ActorType.DELEGATED_USER_WORKLOAD,
            actor_id=f"{principal_type.lower()}:{principal_id}",
            user_id=user_id,
            organization_id=user_ctx.organization_id,
            pod_id=pod_id,
            role_ids=user_ctx.role_ids | workload_ctx.role_ids,
            role_names=user_ctx.role_names | workload_ctx.role_names,
            permission_ids=user_ctx.permission_ids,
            principal_refs=user_ctx.principal_refs | workload_ctx.principal_refs,
            grant_principal_sets=grant_principal_sets,
            workload_principal_refs=workload_ctx.principal_refs,
            delegated_by_user_id=user_id,
            delegation_scope=delegation_scope or frozenset(),
            delegation_session_id=delegation_session_id,
            delegation_actor_name=delegation_actor_name,
            authorizer=Authorizer(self.session),
            request_id=request_id,
        )

    async def build_context_from_delegation_claims(
        self,
        *,
        user_id: UUID,
        claims: DelegationClaims,
        request_id: str | None = None,
        is_default_pod_agent: bool = False,
    ) -> Context:
        return await self.build_delegated_workload_context(
            user_id=user_id,
            principal_type=claims.actor_type.value,
            principal_id=claims.actor_id,
            pod_id=claims.pod_id,
            request_id=request_id,
            is_default_pod_agent=is_default_pod_agent,
            delegation_scope=frozenset(claims.scope),
            delegation_session_id=claims.session_id,
            delegation_actor_name=claims.actor_name,
        )

    async def _ensure_role_with_permissions(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID | None,
        name: str,
        permission_ids: frozenset[str],
        is_system: bool,
        created_by_user_id: UUID | None,
    ) -> bool:
        changed = False
        role = await self._get_role(
            organization_id=organization_id,
            pod_id=pod_id,
            name=name,
        )
        if role is None:
            changed = True
            role = RoleModel(
                organization_id=organization_id,
                pod_id=pod_id,
                name=name,
                is_system=is_system,
                created_by_user_id=created_by_user_id,
            )
            self.session.add(role)
            await self.session.flush()
        changed |= await self._replace_role_permissions(
            role_id=role.id,
            permission_ids=sorted(permission_ids),
            granted_by_user_id=created_by_user_id,
        )
        if changed:
            await self.session.flush()
        return changed

    async def _replace_role_permissions(
        self,
        *,
        role_id: UUID,
        permission_ids: list[str],
        granted_by_user_id: UUID | None,
    ) -> bool:
        desired_permission_ids = sorted(set(permission_ids))
        existing_permission_ids = sorted(
            (
                await self.session.execute(
                    select(RolePermissionModel.permission_id).where(
                        RolePermissionModel.role_id == role_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if existing_permission_ids == desired_permission_ids:
            return False

        await self.session.execute(
            delete(RolePermissionModel).where(RolePermissionModel.role_id == role_id)
        )
        rows = [
            {
                "role_id": role_id,
                "permission_id": permission_id,
                "granted_by_user_id": granted_by_user_id,
            }
            for permission_id in desired_permission_ids
        ]
        if rows:
            await self.session.execute(
                insert(RolePermissionModel)
                .values(rows)
                .on_conflict_do_nothing(
                    constraint="uq_role_permissions_role_permission"
                )
            )
        return True

    async def _get_role(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID | None,
        name: str,
    ) -> RoleModel | None:
        stmt = select(RoleModel).where(
            RoleModel.organization_id == organization_id,
            RoleModel.pod_id == pod_id,
            RoleModel.name == normalize_role_name(name),
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _get_roles_by_name(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID | None,
        names: list[str],
    ) -> list[RoleModel]:
        if not names:
            return []
        stmt = select(RoleModel).where(
            RoleModel.organization_id == organization_id,
            RoleModel.pod_id == pod_id,
            RoleModel.name.in_(names),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _to_summary(self, role: RoleModel) -> RoleSummary:
        stmt = (
            select(RolePermissionModel.permission_id)
            .where(RolePermissionModel.role_id == role.id)
            .order_by(RolePermissionModel.permission_id)
        )
        permission_ids = tuple((await self.session.execute(stmt)).scalars().all())
        return RoleSummary(
            id=role.id,
            organization_id=role.organization_id,
            pod_id=role.pod_id,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permission_ids=permission_ids,
            created_by_user_id=role.created_by_user_id,
            created_at=role.created_at,
        )

    async def _get_org_member(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
    ) -> OrganizationMember | None:
        stmt = select(OrganizationMember).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _get_pod_member(self, *, user_id: UUID, pod_id: UUID) -> PodMember | None:
        stmt = (
            select(PodMember)
            .join(OrganizationMember, PodMember.organization_member_id == OrganizationMember.id)
            .where(PodMember.pod_id == pod_id, OrganizationMember.user_id == user_id)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _load_principal_roles(
        self,
        *,
        principal_type: str,
        principal_id: UUID,
        organization_id: UUID,
        pod_id: UUID | None,
    ) -> list[tuple[UUID, str, str | None]]:
        stmt = (
            select(RoleModel.id, RoleModel.name, RolePermissionModel.permission_id)
            .join(RoleAssignmentModel, RoleAssignmentModel.role_id == RoleModel.id)
            .join(
                RolePermissionModel,
                RolePermissionModel.role_id == RoleModel.id,
                isouter=True,
            )
            .where(
                RoleAssignmentModel.principal_type == principal_type,
                RoleAssignmentModel.principal_id == principal_id,
                RoleModel.organization_id == organization_id,
                RoleModel.pod_id == pod_id,
            )
        )
        return list((await self.session.execute(stmt)).all())

    @staticmethod
    def _merge_role_data(
        rows: list[tuple[UUID, str, str | None]],
        role_ids: set[UUID],
        role_names: set[str],
        permission_ids: set[str],
    ) -> None:
        for role_id, role_name, permission_id in rows:
            role_ids.add(role_id)
            role_names.add(role_name)
            if permission_id is not None:
                permission_ids.add(permission_id)


class Authorizer:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def authorize(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef | None = None,
    ) -> AuthorizationDecision:
        if permission_id not in PERMISSION_BY_ID:
            return AuthorizationDecision(False, "UNKNOWN_PERMISSION", permission_id, resource)
        if ctx.is_superuser:
            return AuthorizationDecision(True, "SUPERUSER", permission_id, resource)
        if ctx.actor_type == ActorType.ANONYMOUS:
            if resource and await self._is_public_read(permission_id, resource):
                return AuthorizationDecision(True, "PUBLIC_RESOURCE", permission_id, resource)
            return AuthorizationDecision(False, "AUTH_REQUIRED", permission_id, resource)

        # The default pod agent mirrors the invoking user's authority but ONLY
        # within its pinned pod: it may exercise any pod-scoped action the user
        # holds here, while org-scoped actions and other pods are denied at this
        # layer and must go through the user-approval-gated tools instead. Named
        # agent/function workloads are not user-equivalent and keep the stricter
        # grant-based path in _authorize_delegated_workload below.
        clamp_to_pod = (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
            and ctx.is_user_equivalent
        )
        if clamp_to_pod and not self._is_pod_scoped_permission(permission_id):
            return AuthorizationDecision(
                False, "DELEGATED_POD_SCOPE_ONLY", permission_id, resource
            )

        if resource is None:
            # Destructive capability checks (no specific resource) must still be
            # gated for delegated workloads, before the user-mirror allows them.
            destructive = await self._destructive_delegated_decision(
                ctx, permission_id, None
            )
            if destructive is not None:
                return destructive
            if not ctx.has_permission(permission_id):
                return AuthorizationDecision(
                    False,
                    "INSUFFICIENT_PERMISSION",
                    permission_id,
                    resource,
                )
            return AuthorizationDecision(True, "PERMISSION_MATCH", permission_id, resource)

        hydrated = await self._hydrate_resource(resource)
        if (
            clamp_to_pod
            and hydrated.pod_id is not None
            and hydrated.pod_id != ctx.pod_id
        ):
            return AuthorizationDecision(
                False, "DELEGATED_POD_SCOPE_ONLY", permission_id, hydrated
            )
        # Destructive actions are gated for EVERY delegated workload — default
        # pod agent and named workloads alike — and this runs before the owner /
        # org-owner / function-self shortcuts so none of them can bypass it. It
        # returns None (proceed) only when the user approved the action for the
        # session or a named workload holds an explicit grant (standing
        # authority); otherwise it denies.
        destructive = await self._destructive_delegated_decision(
            ctx, permission_id, hydrated
        )
        if destructive is not None:
            return destructive
        if self._is_function_self_read(ctx, permission_id, hydrated):
            return AuthorizationDecision(True, "FUNCTION_SELF_READ", permission_id, hydrated)
        if self._is_org_owner_of_pod(ctx, permission_id, hydrated):
            return AuthorizationDecision(True, "ORG_OWNER_POD", permission_id, hydrated)
        if ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD and ctx.workload_principal_refs:
            return await self._authorize_delegated_workload(
                ctx,
                permission_id,
                hydrated,
            )
        if (
            hydrated.owner_user_id is not None
            and hydrated.owner_user_id == ctx.user_id
            and permission_id in owner_actions_for_resource(hydrated.resource_type)
        ):
            return AuthorizationDecision(True, "RESOURCE_OWNER", permission_id, hydrated)
        if not ctx.has_permission(permission_id):
            grant_decision = await self._resource_grant_decision(
                ctx,
                permission_id,
                hydrated,
            )
            if grant_decision is not None:
                return grant_decision
            return AuthorizationDecision(
                False,
                "INSUFFICIENT_PERMISSION",
                permission_id,
                hydrated,
            )
        if hydrated.organization_id is not None and hydrated.pod_id is None:
            if hydrated.organization_id != ctx.organization_id:
                return AuthorizationDecision(
                    False,
                    "ORG_SCOPE_MISMATCH",
                    permission_id,
                    hydrated,
                )
            return AuthorizationDecision(True, "ORG_VISIBLE", permission_id, hydrated)

        visibility = hydrated.visibility or ResourceVisibility.POD
        if visibility == ResourceVisibility.PUBLIC:
            return AuthorizationDecision(True, "PUBLIC_RESOURCE", permission_id, hydrated)
        if hydrated.owner_user_id is not None and hydrated.owner_user_id == ctx.user_id:
            return AuthorizationDecision(True, "RESOURCE_OWNER", permission_id, hydrated)
        if visibility == ResourceVisibility.PERSONAL:
            return AuthorizationDecision(
                False, "PERSONAL_RESOURCE_DENIED", permission_id, hydrated
            )
        if visibility == ResourceVisibility.POD:
            if hydrated.pod_id is not None and hydrated.pod_id != ctx.pod_id:
                return AuthorizationDecision(False, "POD_SCOPE_MISMATCH", permission_id, hydrated)
            return AuthorizationDecision(True, "POD_VISIBLE", permission_id, hydrated)
        if visibility == ResourceVisibility.RESTRICTED:
            grant_decision = await self._resource_grant_decision(
                ctx,
                permission_id,
                hydrated,
            )
            if grant_decision is not None:
                return grant_decision
            return AuthorizationDecision(False, "MISSING_RESOURCE_GRANT", permission_id, hydrated)
        return AuthorizationDecision(False, "UNSUPPORTED_VISIBILITY", permission_id, hydrated)

    async def _destructive_delegated_decision(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef | None,
    ) -> AuthorizationDecision | None:
        """Gate DESTRUCTIVE_ACTIONS for delegated workloads.

        Returns a deny decision when the action must be blocked, or ``None`` to
        let normal authorization proceed. Proceed only when the user recorded a
        session approval (``APPROVE_FOR_SESSION``) for the action type, or a
        named workload holds an explicit grant for it (standing authority — the
        headless path). Applies to the default pod agent and named workloads
        alike, and is called before the owner / org-owner / function-self
        shortcuts so those cannot bypass it.
        """
        if (
            ctx.actor_type != ActorType.DELEGATED_USER_WORKLOAD
            or permission_id not in DESTRUCTIVE_ACTIONS
        ):
            return None
        if await has_session_approval(
            session_id=ctx.delegation_session_id,
            workload_actor_id=ctx.actor_id,
            permission_id=permission_id,
        ):
            return None
        if resource is not None and ctx.workload_principal_refs:
            grant_ids = await self._matching_grant_ids_for_principal_sets(
                ctx, permission_id, resource, (ctx.workload_principal_refs,)
            )
            if grant_ids:
                return None
        return AuthorizationDecision(
            False, "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL", permission_id, resource
        )

    async def _authorize_delegated_workload(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
    ) -> AuthorizationDecision:
        """Grant-first evaluation for named delegated workloads.

        A named delegated workload may perform exactly the actions for which
        the workload itself holds an explicit resource grant — inside its own
        pod and delegation scope. The invoking user's identity is consulted
        only for owner checks (PERSONAL resources) and org-scoped resources
        (workload grants are pod rows, so those fall back to the user's
        capability); data-layer scoping (RLS, ``/me``) also resolves to the
        invoking user. The default pod agent is the opposite: it mirrors the
        invoking user's pod permissions and never reaches this method. Who can
        *trigger* a workload is governed by ``agent.execute`` /
        ``function.execute`` grants on the workload itself.

        DESTRUCTIVE_ACTIONS are the carve-out: with no explicit grant they
        deny with DESTRUCTIVE_ACTION_REQUIRES_APPROVAL unless the user
        recorded a session approval (APPROVE_FOR_SESSION) for the action type.
        """
        if ctx.delegation_scope and not (
            equivalent_permission_ids(permission_id) & ctx.delegation_scope
        ):
            # Implication-expanded so a {function.execute} scope also covers
            # the implied function.read a run needs.
            return AuthorizationDecision(
                False,
                "DELEGATION_SCOPE_VIOLATION",
                permission_id,
                resource,
            )
        if resource.pod_id is not None and resource.pod_id != ctx.pod_id:
            return AuthorizationDecision(False, "POD_SCOPE_MISMATCH", permission_id, resource)
        if resource.organization_id is not None and resource.pod_id is None:
            # Workload grants are pod rows; org-scoped resources fall back to
            # the invoking user's role capability.
            if not ctx.has_permission(permission_id):
                return AuthorizationDecision(
                    False,
                    "INSUFFICIENT_PERMISSION",
                    permission_id,
                    resource,
                )
            if resource.organization_id != ctx.organization_id:
                return AuthorizationDecision(
                    False,
                    "ORG_SCOPE_MISMATCH",
                    permission_id,
                    resource,
                )
            return AuthorizationDecision(True, "ORG_VISIBLE", permission_id, resource)

        visibility = resource.visibility or ResourceVisibility.POD
        if visibility == ResourceVisibility.PERSONAL and resource.owner_user_id != ctx.user_id:
            # Privacy trumps grants: nothing grants a workload access to
            # another user's PERSONAL resource.
            return AuthorizationDecision(
                False,
                "PERSONAL_RESOURCE_DENIED",
                permission_id,
                resource,
            )

        workload_grant_ids = await self._matching_grant_ids_for_principal_sets(
            ctx,
            permission_id,
            resource,
            (ctx.workload_principal_refs,),
        )
        if not workload_grant_ids:
            # A session approval (APPROVE_FOR_SESSION) stands in as an ephemeral
            # grant for anything the user chose to approve for the session.
            # (Destructive actions are already gated earlier in ``authorize``;
            # by the time an ungranted destructive action reaches here it must
            # carry an approval — but check generically so any approved action
            # is honored.)
            if await has_session_approval(
                session_id=ctx.delegation_session_id,
                workload_actor_id=ctx.actor_id,
                permission_id=permission_id,
            ):
                return AuthorizationDecision(
                    True, "SESSION_APPROVAL", permission_id, resource
                )
            return AuthorizationDecision(
                False,
                "MISSING_WORKLOAD_RESOURCE_GRANT",
                permission_id,
                resource,
            )

        if visibility == ResourceVisibility.PUBLIC:
            return AuthorizationDecision(
                True,
                "PUBLIC_RESOURCE",
                permission_id,
                resource,
                matched_grant_ids=tuple(workload_grant_ids),
            )
        if resource.owner_user_id is not None and resource.owner_user_id == ctx.user_id:
            return AuthorizationDecision(
                True,
                "RESOURCE_OWNER",
                permission_id,
                resource,
                matched_grant_ids=tuple(workload_grant_ids),
            )
        if visibility == ResourceVisibility.POD:
            return AuthorizationDecision(
                True,
                "POD_VISIBLE",
                permission_id,
                resource,
                matched_grant_ids=tuple(workload_grant_ids),
            )
        if visibility == ResourceVisibility.RESTRICTED:
            return AuthorizationDecision(
                True,
                "WORKLOAD_RESOURCE_GRANT",
                permission_id,
                resource,
                matched_grant_ids=tuple(workload_grant_ids),
            )
        return AuthorizationDecision(False, "UNSUPPORTED_VISIBILITY", permission_id, resource)

    @staticmethod
    def _is_pod_scoped_permission(permission_id: str) -> bool:
        definition = PERMISSION_BY_ID.get(permission_id)
        return definition is not None and definition.scope == PermissionScope.POD

    @staticmethod
    def _is_org_owner_of_pod(
        ctx: Context, permission_id: str, resource: ResourceRef
    ) -> bool:
        """Org owners have full authority over a pod and everything inside it.

        The pod read/list/delete services already treat the organization owner as
        able to manage any pod in their organization. This mirrors that intent at
        the authorization layer for the pod entity AND its pod-scoped child
        resources (apps, agents, functions, ...), so an org owner whose pod
        authority comes only from the org-owner shortcut can create/deploy/update
        them instead of hitting a 403 the service would have allowed (e.g. the
        app bundle upload that follows app.create). Org-level actions are not
        covered: they still require the org permission itself.
        """
        if ctx.actor_type != ActorType.USER and not ctx.is_user_equivalent:
            return False
        if not Authorizer._is_pod_scoped_permission(permission_id):
            return False
        if ctx.pod_id is None or resource.pod_id != ctx.pod_id:
            return False
        return "ORG_OWNER" in ctx.role_names

    @staticmethod
    def _is_function_self_read(
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
    ) -> bool:
        if permission_id != Permissions.FUNCTION_READ:
            return False
        if resource.resource_type != ResourceType.FUNCTION or resource.resource_id is None:
            return False
        if ctx.actor_type == ActorType.FUNCTION and ctx.actor_id == str(resource.resource_id):
            return True
        if ctx.actor_type != ActorType.DELEGATED_USER_WORKLOAD:
            return False
        return PrincipalRef(WorkloadPrincipalType.FUNCTION.value.upper(), resource.resource_id) in (
            ctx.workload_principal_refs
        )

    async def accessible_resource_ids(
        self,
        ctx: Context,
        permission_id: str,
        resource_type: ResourceType,
        pod_id: UUID,
    ) -> frozenset[UUID]:
        principal_sets = ctx.grant_principal_sets or (ctx.principal_refs,)
        if (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
            and ctx.workload_principal_refs
        ):
            # Grant-first: listings must match _authorize_delegated_workload,
            # which consults the workload's grants alone. Also collapses the
            # per-principal-group query loop to a single query here.
            principal_sets = (ctx.workload_principal_refs,)
        if not principal_sets or any(not group for group in principal_sets):
            return frozenset()
        permission_ids = equivalent_permission_ids(permission_id)
        from sqlalchemy import and_, or_

        visible_ids: set[UUID] | None = None
        for principal_group in principal_sets:
            principal_clauses = [
                and_(
                    ResourcePermissionGrantModel.grantee_type == principal.type,
                    ResourcePermissionGrantModel.grantee_id == principal.id,
                )
                for principal in principal_group
            ]
            resource_type_values = grant_resource_type_values(resource_type)
            stmt = select(ResourcePermissionGrantModel.resource_id).where(
                ResourcePermissionGrantModel.pod_id == pod_id,
                ResourcePermissionGrantModel.resource_type.in_(resource_type_values),
                ResourcePermissionGrantModel.permission_id.in_(permission_ids),
                or_(*principal_clauses),
            )
            group_ids = set((await self.session.execute(stmt)).scalars().all())
            visible_ids = group_ids if visible_ids is None else visible_ids & group_ids
        return frozenset(visible_ids or set())

    async def _is_public_read(self, permission_id: str, resource: ResourceRef) -> bool:
        if not permission_id.endswith(".read"):
            return False
        hydrated = await self._hydrate_resource(resource)
        return hydrated.visibility == ResourceVisibility.PUBLIC

    async def _hydrate_resource(self, resource: ResourceRef) -> ResourceRef:
        if resource.visibility is not None:
            return resource
        if resource.resource_id is None:
            return resource
        # Folder/document hydration also fetches the row's path so folder grants
        # can cascade to descendants in the matcher.
        if resource.resource_type in (ResourceType.FOLDER, ResourceType.DOCUMENT):
            return await self._hydrate_datastore_file(resource)
        if resource.resource_type == ResourceType.CONNECTOR:
            return await self._hydrate_connector(resource)
        if resource.resource_type == ResourceType.CONNECTOR_ACCOUNT:
            return await self._hydrate_connector_account(resource)
        if resource.resource_type == ResourceType.CONNECTOR_AUTH_CONFIG:
            return await self._hydrate_connector_auth_config(resource)
        mapping = {
            ResourceType.AGENT: (
                AgentModel,
                AgentModel.id,
                AgentModel.pod_id,
                AgentModel.user_id,
                AgentModel.visibility,
            ),
            ResourceType.FUNCTION: (
                FunctionModel,
                FunctionModel.id,
                FunctionModel.pod_id,
                FunctionModel.user_id,
                FunctionModel.visibility,
            ),
            ResourceType.CONVERSATION: (
                ConversationModel,
                ConversationModel.id,
                ConversationModel.pod_id,
                ConversationModel.user_id,
                None,
            ),
            ResourceType.DATASTORE_TABLE: (
                DatastoreTable,
                DatastoreTable.id,
                DatastoreTable.pod_id,
                DatastoreTable.user_id,
                DatastoreTable.visibility,
            ),
            ResourceType.FOLDER: (
                DatastoreFile,
                DatastoreFile.id,
                DatastoreFile.pod_id,
                DatastoreFile.owner_user_id,
                DatastoreFile.visibility,
            ),
            ResourceType.DOCUMENT: (
                DatastoreFile,
                DatastoreFile.id,
                DatastoreFile.pod_id,
                DatastoreFile.owner_user_id,
                DatastoreFile.visibility,
            ),
            ResourceType.APP: (
                AppModel,
                AppModel.id,
                AppModel.pod_id,
                AppModel.user_id,
                AppModel.visibility,
            ),
            ResourceType.WORKFLOW: (
                FlowModel,
                FlowModel.id,
                FlowModel.pod_id,
                FlowModel.user_id,
                FlowModel.visibility,
            ),
            ResourceType.SCHEDULE: (
                Schedule,
                Schedule.id,
                Schedule.pod_id,
                Schedule.user_id,
                Schedule.visibility,
            ),
        }
        if resource.resource_type not in mapping:
            return resource
        _model, id_col, pod_col, owner_col, visibility_col = mapping[resource.resource_type]
        if visibility_col is None:
            stmt = select(pod_col, owner_col).where(id_col == resource.resource_id)
        else:
            stmt = select(pod_col, owner_col, visibility_col).where(id_col == resource.resource_id)
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        if visibility_col is None:
            visibility = ResourceVisibility.PERSONAL
        else:
            visibility = self._normalize_visibility(row[2])
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id,
            pod_id=resource.pod_id or row[0],
            owner_user_id=resource.owner_user_id or row[1],
            visibility=visibility,
        )

    async def _hydrate_datastore_file(self, resource: ResourceRef) -> ResourceRef:
        """Hydrate a FOLDER/DOCUMENT ref, including its path so folder grants
        can cascade to descendants."""
        stmt = select(
            DatastoreFile.pod_id,
            DatastoreFile.owner_user_id,
            DatastoreFile.visibility,
            DatastoreFile.path,
        ).where(DatastoreFile.id == resource.resource_id)
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id,
            pod_id=resource.pod_id or row[0],
            owner_user_id=resource.owner_user_id or row[1],
            visibility=resource.visibility or self._normalize_visibility(row[2]),
            path=resource.path or row[3],
        )

    async def _hydrate_connector(self, resource: ResourceRef) -> ResourceRef:
        if resource.pod_id is None:
            return resource
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id,
            pod_id=resource.pod_id,
            owner_user_id=resource.owner_user_id,
            # Connectors are org-wide capability resources, always
            # available to everyone: a grant on the app is a capability grant
            # ("may use this app type"), never a sharing grant, so it must not
            # restrict the app. The real access boundary is the connected
            # *account*, which is user-owned and enforced separately in
            # ``account_resolution_service`` (own account is returned directly;
            # someone else's requires ``connector_account.use``). Keeping the
            # app POD-visible means a workload holding ``connector.use``
            # is gated on that capability alone, regardless of which user the run
            # is delegated for -- the regression that denied delegated runs for
            # any member who did not also personally hold an app grant.
            visibility=ResourceVisibility.POD,
        )

    async def _hydrate_connector_account(self, resource: ResourceRef) -> ResourceRef:
        stmt = select(
            Account.organization_id,
            Account.user_id,
        ).where(
            Account.id == resource.resource_id,
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        pod_id = resource.pod_id
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id or row[0],
            pod_id=pod_id,
            owner_user_id=resource.owner_user_id or row[1],
            visibility=(
                await self._visibility_from_optional_grants(
                    pod_id=pod_id,
                    resource_type=resource.resource_type,
                    resource_id=resource.resource_id,
                )
                if pod_id is not None
                else resource.visibility
            ),
        )

    async def _hydrate_connector_auth_config(self, resource: ResourceRef) -> ResourceRef:
        stmt = select(AuthConfig.organization_id, AuthConfig.created_by_user_id).where(
            AuthConfig.id == resource.resource_id
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id or row[0],
            pod_id=resource.pod_id,
            owner_user_id=resource.owner_user_id or row[1],
            visibility=resource.visibility,
        )

    async def _visibility_from_optional_grants(
        self,
        *,
        pod_id: UUID,
        resource_type: ResourceType,
        resource_id: UUID,
    ) -> ResourceVisibility:
        conditions = [
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type.in_(
                grant_resource_type_values(resource_type)
            ),
            ResourcePermissionGrantModel.resource_id == resource_id,
        ]
        grant_exists_stmt = select(exists().where(*conditions))
        has_grants = (await self.session.execute(grant_exists_stmt)).scalar_one()
        if has_grants:
            return ResourceVisibility.RESTRICTED
        return ResourceVisibility.POD

    async def _resource_grant_decision(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
    ) -> AuthorizationDecision | None:
        visibility = resource.visibility or ResourceVisibility.POD
        if visibility == ResourceVisibility.PERSONAL and resource.owner_user_id != ctx.user_id:
            return None
        grant_ids = await self._matching_grant_ids(ctx, permission_id, resource)
        if not grant_ids:
            return None
        return AuthorizationDecision(
            True,
            "RESOURCE_GRANT_MATCH",
            permission_id,
            resource,
            matched_grant_ids=tuple(grant_ids),
        )

    async def _matching_grant_ids(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
    ) -> list[UUID]:
        return await self._matching_grant_ids_for_principal_sets(
            ctx,
            permission_id,
            resource,
            ctx.grant_principal_sets or (ctx.principal_refs,),
        )

    @staticmethod
    def _ancestor_folder_paths(path: str) -> list[str]:
        """Return the ancestor folder paths of ``path`` (excluding itself).

        ``/a/b/c.md`` -> ``["/", "/a", "/a/b"]``. The root ``/`` is included so
        a grant on the (optional) root folder cascades pod-wide.
        """
        segments = [segment for segment in path.split("/") if segment]
        paths = ["/"]
        accumulated = ""
        for segment in segments[:-1]:
            accumulated = f"{accumulated}/{segment}"
            paths.append(accumulated)
        return paths

    async def _acceptable_grant_resource_ids(
        self, resource: ResourceRef
    ) -> list[UUID] | None:
        """Resource ids a grant may target to authorize ``resource``.

        For hierarchical FOLDER/DOCUMENT resources this is ``{self id} ∪
        {ancestor folder ids} ∪ {pod id (pod-wide root grant)}`` so a grant
        on the resource itself or any ancestor folder cascades down. Returns
        ``None`` for every other resource type, signalling the caller to keep
        exact-id matching.
        """
        if resource.resource_type not in (ResourceType.FOLDER, ResourceType.DOCUMENT):
            return None
        if resource.pod_id is None:
            return [resource.resource_id] if resource.resource_id else []
        acceptable: set[UUID] = set()
        if resource.resource_id is not None:
            acceptable.add(resource.resource_id)
        # A grant keyed on the pod id itself is the pod-wide document grant.
        acceptable.add(resource.pod_id)
        if resource.path:
            # Resolve grant-target ids by path for this resource's OWN folder
            # plus every ancestor folder. Including the resource's own path is
            # what lets a grant *on* the folder authorize the folder itself when
            # the caller authorizes by path only -- e.g. the list_files /
            # search-scope pre-check passes resource_name but no resource_id, so
            # ``_require_document_action`` falls back to the pod id and the
            # self-grant would otherwise never match. This mirrors the SQL
            # projection's self-match (``resource_path_col == granted.path``).
            candidate_paths = [*self._ancestor_folder_paths(resource.path), resource.path]
            stmt = select(DatastoreFile.id).where(
                DatastoreFile.pod_id == resource.pod_id,
                DatastoreFile.path.in_(candidate_paths),
            )
            acceptable.update(
                (await self.session.execute(stmt)).scalars().all()
            )
        return list(acceptable)

    async def _matching_grant_ids_for_principal_sets(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
        principal_sets: tuple[frozenset[PrincipalRef], ...],
    ) -> list[UUID]:
        if resource.pod_id is None or resource.resource_id is None:
            return []
        permission_ids = equivalent_permission_ids(permission_id)
        if not principal_sets or any(not group for group in principal_sets):
            return []
        from sqlalchemy import or_, and_

        # For folders/documents, a grant on any ancestor folder (or the pod-wide
        # root grant) cascades down; every other resource type stays exact-match.
        acceptable_ids = await self._acceptable_grant_resource_ids(resource)
        if acceptable_ids is None:
            resource_id_clause = (
                ResourcePermissionGrantModel.resource_id == resource.resource_id
            )
        elif not acceptable_ids:
            return []
        else:
            resource_id_clause = ResourcePermissionGrantModel.resource_id.in_(
                acceptable_ids
            )

        matched_ids: list[UUID] = []
        for principal_group in principal_sets:
            clauses = [
                (
                    ResourcePermissionGrantModel.grantee_type == principal.type,
                    ResourcePermissionGrantModel.grantee_id == principal.id,
                )
                for principal in principal_group
            ]
            resource_type_values = grant_resource_type_values(resource.resource_type)
            stmt = select(ResourcePermissionGrantModel.id).where(
                ResourcePermissionGrantModel.pod_id == resource.pod_id,
                ResourcePermissionGrantModel.resource_type.in_(resource_type_values),
                resource_id_clause,
                ResourcePermissionGrantModel.permission_id.in_(permission_ids),
                or_(*(and_(*clause) for clause in clauses)),
            )
            group_ids = list((await self.session.execute(stmt)).scalars().all())
            if not group_ids:
                return []
            matched_ids.extend(group_ids)
        return matched_ids

    @staticmethod
    def _normalize_visibility(value: str | None) -> ResourceVisibility:
        if value is None:
            return ResourceVisibility.POD
        normalized = str(value).upper()
        if normalized in {"PUBLIC"}:
            return ResourceVisibility.PUBLIC
        if normalized in {"PERSONAL", "PRIVATE", "OWNER"}:
            return ResourceVisibility.PERSONAL
        if normalized in {"RESTRICTED"}:
            return ResourceVisibility.RESTRICTED
        return ResourceVisibility.POD

