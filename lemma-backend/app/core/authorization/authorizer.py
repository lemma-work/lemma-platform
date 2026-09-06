"""The authorization decision engine.

Split out of `service.py`, which held this class and `AuthorizationDataService`
in 1926 lines. They share nothing but an `AsyncSession`: nothing here calls the
data service, and the dependency runs one way -- the data service builds an
`Authorizer` in three places, which is why the import points from there to here
and not back.

`app/core/authorization/` already had twenty sibling modules; `service.py` was
the one that never got split.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
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
from app.core.authorization.delegation import (
    DESTRUCTIVE_ACTIONS,
    WorkloadPrincipalType,
)
from app.core.authorization.session_approvals import has_session_approval


from app.core.authorization.grants import (
    grant_resource_type_values,
)
from app.core.authorization.models import (
    ResourcePermissionGrantModel,
)
from app.core.authorization.grant_resolution import GrantResolutionMixin
from app.core.authorization.hydration import ResourceHydrationMixin
from app.core.authorization.permissions import (
    PERMISSION_BY_ID,
    PermissionScope,
    Permissions,
    equivalent_permission_ids,
)
from app.core.authorization.resource_actions import owner_actions_for_resource
from app.core.authorization.workload_authority import authorize_delegated_workload
from app.core.authorization.resource_tables import (
    RESOURCE_TABLES,
    pod_is_unknowable,
)


async def _session_approval(
    ctx: "Context",
    *,
    session_id: str | None,
    workload_actor_id: str | None,
    permission_id: str,
) -> bool:
    """``has_session_approval``, asked at most once per permission per request.

    The lookup is a Redis round trip made while the request's pooled database
    connection is checked out, so repeating it per check is the expensive part.

    Caching it means an approval granted *during* a request is not seen by that
    request. That is correct for an ordinary request, which is short; for a
    long-lived streamed one it means the approval takes effect on the next call
    rather than mid-stream. Acceptable because approvals are awaited through the
    approval flow rather than polled here -- but it is a staleness window, not
    an invariant.
    """
    cached = ctx._session_approval_cache.get(permission_id)  # noqa: SLF001
    if cached is not None:
        return cached
    approved = await has_session_approval(
        session_id=session_id,
        workload_actor_id=workload_actor_id,
        permission_id=permission_id,
    )
    ctx._session_approval_cache[permission_id] = approved  # noqa: SLF001
    return approved


#: Datastore's file columns, via the one table that names them. `FOLDER` and
#: `DOCUMENT` share a row, so either serves.
_FILES = RESOURCE_TABLES[ResourceType.FOLDER]


class Authorizer(ResourceHydrationMixin, GrantResolutionMixin):
    def __init__(self, session: AsyncSession):
        self.session = session
        # Grant rows for one (pod, resource-target set, principal group), keyed
        # without the permission. See `_grant_rows_for_principal_group`.
        self._grant_rows: dict[
            tuple[UUID, tuple[str, ...], tuple[UUID, ...], frozenset[PrincipalRef]],
            list[tuple[UUID, str]],
        ] = {}
        # Ancestor-folder ids by (pod, candidate paths). See
        # `_acceptable_grant_resource_ids`.
        self._folder_ids_by_paths: dict[tuple[UUID, tuple[str, ...]], list[UUID]] = {}

    async def _describe_resource(self, resource: ResourceRef | None) -> str | None:
        """Human name for a denied resource, or None when it can't be resolved.

        Runs only on the denial path, so the extra lookup costs nothing in the
        allow case. Folders and documents already carry their path; everything
        else resolves id -> name through the same registry the grant APIs use.
        """
        if resource is None:
            return None
        if resource.path:
            return resource.path
        if resource.resource_id is None or resource.pod_id is None:
            return None
        try:
            from app.core.authorization.resource_names import (
                resolve_resource_names_by_ids,
            )

            resolved = await resolve_resource_names_by_ids(
                self.session,
                pod_id=resource.pod_id,
                refs=[(resource.resource_type, resource.resource_id)],
            )
        except Exception:  # noqa: BLE001 — a nicer message is never worth an error
            return None
        return resolved.get((resource.resource_type, resource.resource_id))

    async def authorize(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef | None = None,
    ) -> AuthorizationDecision:
        if permission_id not in PERMISSION_BY_ID:
            return AuthorizationDecision(
                False, "UNKNOWN_PERMISSION", permission_id, resource
            )
        if ctx.is_superuser:
            return AuthorizationDecision(True, "SUPERUSER", permission_id, resource)
        if ctx.actor_type == ActorType.ANONYMOUS:
            if resource and await self._is_public_read(permission_id, resource):
                return AuthorizationDecision(
                    True, "PUBLIC_RESOURCE", permission_id, resource
                )
            return AuthorizationDecision(
                False, "AUTH_REQUIRED", permission_id, resource
            )

        # The default pod agent mirrors the invoking user's authority but ONLY
        # within its pinned pod: it may exercise any pod-scoped action the user
        # holds here, while org-scoped actions and other pods are denied at this
        # layer and must go through the user-approval-gated tools instead. Named
        # agent/function workloads are not user-equivalent and take the
        # stricter intersection path in ``workload_authority`` below.
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
            return AuthorizationDecision(
                True, "PERMISSION_MATCH", permission_id, resource
            )

        hydrated = await self._hydrate_resource(resource)
        if clamp_to_pod and (
            hydrated.pod_id != ctx.pod_id
            if hydrated.pod_id is not None
            # A pod-scoped resource whose pod could not be established is
            # refused rather than waved through. This used to read
            # `pod_id is not None and pod_id != ctx.pod_id`, so a type
            # hydration did not know skipped the clamp entirely — the guard
            # confining a pod's default agent to its own pod was opt-out by
            # omission. `_pod_is_unknowable` exempts the types that genuinely
            # have no pod, and `_assert_every_resource_type_is_classified`
            # makes staying silent about a new one impossible.
            else pod_is_unknowable(hydrated)
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
            return AuthorizationDecision(
                True, "FUNCTION_SELF_READ", permission_id, hydrated
            )
        if self._is_org_owner_of_pod(ctx, permission_id, hydrated):
            return AuthorizationDecision(True, "ORG_OWNER_POD", permission_id, hydrated)
        if (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
            and ctx.workload_principal_refs
        ):
            return await authorize_delegated_workload(
                self,
                ctx,
                permission_id,
                hydrated,
            )
        if (
            hydrated.owner_user_id is not None
            and hydrated.owner_user_id == ctx.user_id
            and permission_id in owner_actions_for_resource(hydrated.resource_type)
        ):
            return AuthorizationDecision(
                True, "RESOURCE_OWNER", permission_id, hydrated
            )
        visibility_decision = self._visibility_read_decision(
            ctx, permission_id, hydrated
        )
        if visibility_decision is not None:
            return visibility_decision
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
            return AuthorizationDecision(
                True, "PUBLIC_RESOURCE", permission_id, hydrated
            )
        if hydrated.owner_user_id is not None and hydrated.owner_user_id == ctx.user_id:
            return AuthorizationDecision(
                True, "RESOURCE_OWNER", permission_id, hydrated
            )
        if visibility == ResourceVisibility.PERSONAL:
            return AuthorizationDecision(
                False, "PERSONAL_RESOURCE_DENIED", permission_id, hydrated
            )
        if visibility == ResourceVisibility.POD:
            if hydrated.pod_id is not None and hydrated.pod_id != ctx.pod_id:
                return AuthorizationDecision(
                    False, "POD_SCOPE_MISMATCH", permission_id, hydrated
                )
            return AuthorizationDecision(True, "POD_VISIBLE", permission_id, hydrated)
        if visibility == ResourceVisibility.RESTRICTED:
            grant_decision = await self._resource_grant_decision(
                ctx,
                permission_id,
                hydrated,
            )
            if grant_decision is not None:
                return grant_decision
            return AuthorizationDecision(
                False, "MISSING_RESOURCE_GRANT", permission_id, hydrated
            )
        return AuthorizationDecision(
            False, "UNSUPPORTED_VISIBILITY", permission_id, hydrated
        )

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

        ``None`` here is only a pass through *this* gate: a named workload then
        still has to clear the invoker ceiling in ``workload_authority``, so an
        approval unlocks a destructive action without conferring authority the
        approving person does not have.
        """
        if (
            ctx.actor_type != ActorType.DELEGATED_USER_WORKLOAD
            or permission_id not in DESTRUCTIVE_ACTIONS
        ):
            return None
        if await _session_approval(
            ctx,
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

    @staticmethod
    def _is_read_permission(permission_id: str) -> bool:
        return permission_id.endswith(".read")

    def _visibility_read_decision(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
    ) -> AuthorizationDecision | None:
        """Allow a read the resource's own visibility already permits.

        Visibility above POD is a property of the resource, not of the viewer's
        role, so it must be evaluated before the pod-permission gate below. It
        used to sit *after* that gate, which made PUBLIC unreachable: a non-member
        holds no pod permissions, failed the gate, and was denied before the
        PUBLIC branch ever ran. The level changed nothing for members (who match
        POD anyway) and granted nothing to anyone else — while ANONYMOUS, checked
        earlier in ``authorize``, did get through. Signed-out beat signed-in.

        Deliberately narrow: reads only, humans only. Write-shaped actions and
        every workload actor fall through to the paths they already took, so
        flipping a resource to PUBLIC can never widen what an agent or function
        may do, nor let anyone edit what they can now read.
        """
        if ctx.actor_type != ActorType.USER or ctx.user_id is None:
            return None
        if not self._is_read_permission(permission_id):
            return None
        if resource.pod_id is not None and resource.pod_id != ctx.pod_id:
            return None

        visibility = resource.visibility or ResourceVisibility.POD
        if visibility == ResourceVisibility.PUBLIC:
            return AuthorizationDecision(
                True, "PUBLIC_RESOURCE", permission_id, resource
            )
        return None

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
        if (
            resource.resource_type != ResourceType.FUNCTION
            or resource.resource_id is None
        ):
            return False
        if ctx.actor_type == ActorType.FUNCTION and ctx.actor_id == str(
            resource.resource_id
        ):
            return True
        if ctx.actor_type != ActorType.DELEGATED_USER_WORKLOAD:
            return False
        return PrincipalRef(
            WorkloadPrincipalType.FUNCTION.value.upper(), resource.resource_id
        ) in (ctx.workload_principal_refs)

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
            # The workload's own grants: the FIRST half of the intersection
            # ``workload_authority`` applies. The second half — could
            # the invoking person reach it themselves — is not expressible as a
            # grant query, because their access also comes from role
            # permissions, ownership and visibility. So this narrows to what
            # the workload was granted and nothing more; a caller listing for a
            # delegated workload must still authorize each id it shows, which
            # is where the person's ceiling is applied. Also collapses the
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
