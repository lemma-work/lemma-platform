"""Authorization data service and authorizer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bounded import BoundedSet
from app.core.authorization.context import (
    ActorType,
    Context,
    PrincipalRef,
    ResourceRef,
    ResourceType,
)
from app.core.authorization.cache import (
    RoleSnapshot,
    get_role_snapshot,
    invalidate_role_snapshot_cache,
    set_role_snapshot,
)
from app.core.authorization.delegation import (
    DelegationClaims,
)


from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.authorization.grants import (
    delete_grantee_grants,
)
from app.core.authorization.models import (
    AuthPermissionModel,
    RoleAssignmentModel,
    RoleModel,
    RolePermissionModel,
)
from app.core.authorization.permissions import (
    PERMISSION_BY_ID,
    PERMISSION_DEFINITIONS,
    SYSTEM_ROLE_PERMISSIONS,
)
from app.core.authorization.authorizer import Authorizer
from app.core.authorization.resource_names import resolve_resource_id_by_name
from app.core.authorization.resource_tables import (
    RESOURCE_TABLES,
)
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.core.authorization.roles import (
    SYSTEM_POD_ROLE_NAMES,
    normalize_role_list,
    normalize_role_name,
)
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember


SYSTEM_ORG_ROLES = {"ORG_MEMBER", "ORG_EDITOR", "ORG_OWNER"}
SYSTEM_POD_ROLES = SYSTEM_POD_ROLE_NAMES

# Scopes whose system roles are known to be fully provisioned. Entries are only
# added when an ensure pass found nothing to write, so a rolled-back transaction
# can never mark a scope as provisioned.
#
# Bounded: one entry per (organization, pod) on a process that runs for hours is
# strictly monotonic, and the memo only saves a round trip -- re-ensuring an
# already-provisioned scope is a no-op, so forgetting one costs nothing.
_ENSURED_ROLE_SCOPES: BoundedSet[tuple[UUID, UUID | None]] = BoundedSet(4096)


@dataclass(frozen=True, slots=True)
class MemberAuthorizationTargets:
    """Authorization data tied to an org member, captured before removal so the
    (FK-less, non-cascading) role assignments/grants can be purged afterward."""

    user_id: UUID | None
    organization_member_id: UUID
    # (pod_member_id, pod_id) for each pod membership under this org member.
    pod_memberships: tuple[tuple[UUID, UUID], ...]


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
        """Upsert every definition in PERMISSION_DEFINITIONS, ON CONFLICT DO NOTHING.

        Was check-then-insert: read existing ids, then session.add() whatever
        was missing. Called from _ensure_system_roles on every org/pod
        creation, against a table with no per-scope key to serialize two
        callers on -- two organizations created at genuinely the same
        moment, against a database not yet fully seeded, could both decide
        the same definition was missing and both try to insert it, one
        losing to "duplicate key value violates unique constraint
        auth_permissions_pkey" (confirmed under real concurrent load, not
        just a synthetic repro). A single bulk upsert closes the same TOCTOU
        window the role_permissions upsert below already closes for its own
        table, and rowcount reports exactly the same "was anything new"
        signal the loop above tracked in `changed`.
        """
        rows = [
            {
                "id": definition.id,
                "scope": definition.scope.value,
                "resource_type": definition.resource_type,
                "description": definition.description,
                "system_only": definition.system_only,
            }
            for definition in PERMISSION_DEFINITIONS
        ]
        if not rows:
            return False
        result = await self.session.execute(
            insert(AuthPermissionModel)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        return result.rowcount > 0

    async def ensure_org_system_roles(self, organization_id: UUID) -> None:
        await self._ensure_system_roles(
            organization_id=organization_id,
            pod_id=None,
            role_names=SYSTEM_ORG_ROLES,
        )

    async def ensure_pod_system_roles(
        self, *, organization_id: UUID, pod_id: UUID
    ) -> None:
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
            .where(
                RoleModel.organization_id == organization_id, RoleModel.pod_id == pod_id
            )
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
        await self._invalidate_snapshots_after_commit(
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
        await self._invalidate_snapshots_after_commit(
            organization_id=organization_id, pod_id=pod_id
        )

    async def _invalidate_snapshots_after_commit(
        self,
        *,
        organization_id: UUID | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> None:
        """Drop the affected snapshots once the mutation has actually committed.

        Not inline: invalidating inside the transaction holds the connection
        across a Redis round trip, and it is the wrong order besides — between
        the invalidation and the commit a concurrent reader can repopulate the
        cache from the state this mutation is about to replace.

        Falls back to invalidating immediately when there is no unit of work to
        defer to (a service constructed straight from a session), because a
        stale snapshot is worse than an early one.
        """
        uow = self.session.info.get("lemma_uow")

        async def _run() -> None:
            await invalidate_role_snapshot_cache(
                organization_id=organization_id, pod_id=pod_id, user_id=user_id
            )

        if uow is None:
            # No unit of work to defer to (a service built straight from a
            # session). A stale snapshot is worse than an early invalidation.
            await _run()
            return
        uow.after_commit(_run)

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
        table = RESOURCE_TABLES.get(resource_type)
        if table is None:
            return None
        stmt = select(table.owner_column).where(table.id_column == resource_id)
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
                RoleAssignmentModel.role_id.in_(
                    select(RoleModel.id).where(
                        RoleModel.organization_id == organization_id,
                        RoleModel.pod_id == pod_id,
                    )
                ),
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
        # Targeted invalidation: an assignment change touches exactly one
        # principal, so drop only that principal's snapshots rather than flushing
        # every user's. Falls back to a full clear when the principal can't be
        # mapped to its snapshot key (safe superset).
        snapshot_principal_id = await self._resolve_snapshot_principal_id(
            principal_type, principal_id
        )
        await self._invalidate_snapshots_after_commit(
            organization_id=organization_id,
            pod_id=pod_id,
            user_id=snapshot_principal_id,
        )
        return normalized

    async def _cache_snapshot_without_holding(
        self, user_id: UUID, snapshot: RoleSnapshot
    ) -> None:
        """Write the derived snapshot to Redis with no pooled connection held.

        The read side is already free: ``get_role_snapshot`` runs before this
        service touches the session, so a cache hit costs no database time at
        all. The write is the opposite -- it lands *after* the pod, membership
        and role reads that derive the snapshot, so the connection those reads
        checked out is still held across a Redis round trip.

        It looks small, and it was the single largest entry in the connection
        gate's baseline: 37 of 108 violations, because ``build_user_context``
        propagates through ``build_delegated_workload_context`` into every
        caller across twenty files.

        Committing first returns the connection. ``safe_to_release`` decides
        whether that is allowed -- it refuses when the caller has pending or
        flushed writes, staged outbox events, or a transaction-scoped advisory
        lock. When it refuses we write anyway and keep the old behaviour: a
        cache miss that had to hold is strictly better than a caller whose
        transaction we ended underneath it.
        """
        async with connection_released(getattr(self, "session", None)):
            await set_role_snapshot(user_id=user_id, snapshot=snapshot)

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

        # Ask the cache FIRST. The pod-scoped key does not include the
        # organization (see ``_snapshot_suffix``), so a hit needs no database
        # read at all — the snapshot carries the organization in its payload.
        # This used to read the Pod row first, purely to build the key, and paid
        # for it on every pod request no matter how warm the cache was.
        async with connection_released(getattr(self, "session", None)):
            cached = await get_role_snapshot(
                user_id=user_id,
                organization_id=organization_id,
                pod_id=pod_id,
            )
        pod_is_deleted = False
        if cached is None and pod_id is not None:
            # Miss: the snapshot has to be derived, and that needs the org.
            pod = await self.session.get(Pod, pod_id)
            if pod is not None:
                organization_id = pod.organization_id
            # The row is already in hand, so learning whether the pod was
            # deleted costs nothing here, and this is the only place it is read.
            # `get_pod_context` turns it into the refusal.
            #
            # A *missing* row is deliberately not "deleted". This verdict is
            # cached in the role snapshot, so treating absence as deletion means
            # one moment where the row is not visible to this session — a create
            # not yet committed, a transaction boundary, replica lag — is
            # remembered as "deleted" and 404s every pod-scoped request for the
            # rest of the cache's life. A pod that genuinely does not exist is
            # already 404'd by the routes that read it, which is the right place
            # for that answer because it is not cached.
            pod_is_deleted = pod is not None and bool(pod.is_deleted)

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
                pod_is_deleted=cached.pod_is_deleted,
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
                self._merge_role_data(
                    org_role_data, role_ids, role_names, permission_ids
                )

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
                self._merge_role_data(
                    pod_role_data, role_ids, role_names, permission_ids
                )

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
            pod_is_deleted=pod_is_deleted,
        )
        await self._cache_snapshot_without_holding(user_id, snapshot)
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
            pod_is_deleted=snapshot.pod_is_deleted,
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
        normalized_principal_type = principal_type.upper()
        actor_type = (
            ActorType.AGENT
            if normalized_principal_type == "AGENT"
            else ActorType.FUNCTION
        )

        # Ask the cache FIRST, exactly as the user path does. The pod-scoped key
        # omits the organization (see ``_snapshot_suffix``), so a hit needs no
        # database read: the snapshot carries the organization in its payload.
        # This read the Pod row first purely to build the key, and every agent
        # tool call that touches a pod, datastore or connector paid for it no
        # matter how warm the cache was.
        #
        # The role snapshot cache is keyed by principal id; workload principals
        # (agent/function ids) share it with user ids without collision.
        async with connection_released(getattr(self, "session", None)):
            cached = await get_role_snapshot(
                user_id=principal_id,
                organization_id=None,
                pod_id=pod_id,
            )
        organization_id: UUID | None = None
        if cached is None:
            # Miss: deriving the snapshot needs the organization.
            pod = await self.session.get(Pod, pod_id)
            organization_id = pod.organization_id if pod is not None else None
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
        await self._cache_snapshot_without_holding(principal_id, snapshot)
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
            # The INVOKING PERSON's permissions, deliberately not unioned with
            # the workload's role permissions. Together with the two
            # ``invoker_*`` fields below this is the person's half of the
            # intersection PS-ACCESS-020 promises, and
            # ``workload_authority`` reads all three as one set. A
            # union here would silently raise the ceiling.
            permission_ids=user_ctx.permission_ids,
            principal_refs=user_ctx.principal_refs | workload_ctx.principal_refs,
            grant_principal_sets=grant_principal_sets,
            workload_principal_refs=workload_ctx.principal_refs,
            invoker_principal_refs=user_ctx.principal_refs,
            invoker_role_names=user_ctx.role_names,
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
        # Defense in depth: the claims and the session are minted together, so
        # the claim's invoked_by_user_id must match the authenticated session
        # user. A mismatch means the token was tampered with or mis-minted — never
        # build a context that delegates for a different user than the token.
        if claims.invoked_by_user_id != user_id:
            raise DomainError(
                "Delegation invoked_by_user_id does not match the session user",
                code="DELEGATION_USER_MISMATCH",
                status_code=403,
            )
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
            .join(
                OrganizationMember,
                PodMember.organization_member_id == OrganizationMember.id,
            )
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

    async def _resolve_snapshot_principal_id(
        self, principal_type: str, principal_id: UUID
    ) -> UUID | None:
        """Map a role-assignment principal to the id its role snapshot is cached
        under (see ``cache._snapshot_suffix``), or ``None`` when it can't be
        resolved so the caller falls back to a full-cache invalidation.

        Workload snapshots are cached under the workload principal id itself;
        org/pod member snapshots are cached under the human ``user_id``.
        """
        normalized = principal_type.upper()
        if normalized in ("AGENT", "FUNCTION"):
            return principal_id
        if normalized == "ORG_MEMBER":
            return (
                await self.session.execute(
                    select(OrganizationMember.user_id).where(
                        OrganizationMember.id == principal_id
                    )
                )
            ).scalar_one_or_none()
        if normalized == "POD_MEMBER":
            return (
                await self.session.execute(
                    select(OrganizationMember.user_id)
                    .join(
                        PodMember,
                        PodMember.organization_member_id == OrganizationMember.id,
                    )
                    .where(PodMember.id == principal_id)
                )
            ).scalar_one_or_none()
        return None

    async def delete_principal_role_assignments(
        self, *, principal_type: str, principal_id: UUID
    ) -> None:
        """Delete every role assignment held by a principal.

        ``RoleAssignmentModel.principal_id`` is polymorphic and carries no FK, so
        these rows do not cascade when the underlying member/workload is deleted.
        Call this on removal to avoid orphaned assignments. Parallel to
        ``delete_grantee_grants`` for resource grants.
        """
        await self.session.execute(
            delete(RoleAssignmentModel).where(
                RoleAssignmentModel.principal_type == principal_type,
                RoleAssignmentModel.principal_id == principal_id,
            )
        )
        await self.session.flush()

    async def member_authorization_targets(
        self, *, organization_member_id: UUID
    ) -> "MemberAuthorizationTargets | None":
        """Snapshot the authorization data tied to an org member before it is
        removed: the human ``user_id`` and the pod memberships that will
        cascade-delete with it (their role assignments/grants do not cascade).

        Returns ``None`` when the org member does not exist. Read-only, so it is
        safe to call before the removal is authorized.
        """
        row = (
            await self.session.execute(
                select(OrganizationMember.user_id).where(
                    OrganizationMember.id == organization_member_id
                )
            )
        ).first()
        if row is None:
            return None
        pod_rows = (
            await self.session.execute(
                select(PodMember.id, PodMember.pod_id).where(
                    PodMember.organization_member_id == organization_member_id
                )
            )
        ).all()
        return MemberAuthorizationTargets(
            user_id=row[0],
            organization_member_id=organization_member_id,
            pod_memberships=tuple((r[0], r[1]) for r in pod_rows),
        )

    async def purge_member_authorization(
        self, targets: "MemberAuthorizationTargets"
    ) -> None:
        """Delete the role assignments + resource grants for a removed org member
        and its (cascade-deleted) pod memberships, then invalidate the removed
        user's cached role snapshots so access is revoked on the next request."""
        for pod_member_id, pod_id in targets.pod_memberships:
            await self.delete_principal_role_assignments(
                principal_type="POD_MEMBER", principal_id=pod_member_id
            )
            await delete_grantee_grants(
                self.session,
                pod_id=pod_id,
                grantee_type="POD_MEMBER",
                grantee_id=pod_member_id,
            )
        await self.delete_principal_role_assignments(
            principal_type="ORG_MEMBER",
            principal_id=targets.organization_member_id,
        )
        await self._invalidate_snapshots_after_commit(user_id=targets.user_id)
