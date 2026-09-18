"""Authorization contexts for a listing that spans many pods.

``AuthorizationDataService.build_user_context`` answers for one pod and costs a
role load whenever that pod's snapshot is cold. A listing that spans every pod
in an organization cannot call it in a loop: that is a fan-out per pod, which is
exactly what those listings exist to remove, and it stays invisible until a cold
cache turns one query into four for every pod on the page.

So every role behind the caller's memberships is loaded for the whole set at
once and the contexts are assembled from that. **One query, whatever the pod
count** -- the memberships themselves are not looked up here, because a listing
that spans pods has already had to find them to know which pods it is listing.
Passing them in is what keeps this module free of the identity and pod ORM.

**These are for projecting a listing, not for deciding a request.** Nothing here
reads or writes the role-snapshot cache, deliberately: a cache write from a bulk
builder would let one listing's view of a pod become the cached answer for every
later authorization in it. A route that authorizes one pod's own request goes on
using ``build_user_context`` through :mod:`app.core.authorization.factory`.

Its own module rather than another method on ``AuthorizationDataService``: that
file is already at the architecture ratchet's per-file ceiling, so the
extraction is forced -- but the seam is real either way, because "what may this
listing show" and "may this request proceed" want different caching.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.authorizer import Authorizer
from app.core.authorization.context import ActorType, Context, PrincipalRef
from app.core.authorization.role_queries import (
    ANY_POD,
    RoleRow,
    load_roles_for_principals,
    merge_role_data,
    roles_applying_to_pod,
)


async def build_listing_contexts_for_pods(
    session: AsyncSession,
    *,
    user_id: UUID,
    organization_id: UUID,
    organization_member_id: UUID | None,
    pod_member_ids_by_pod: Mapping[UUID, UUID | None],
    request_id: str | None = None,
) -> dict[UUID, Context]:
    """One context per pod, in one query.

    ``pod_member_ids_by_pod`` is every pod to build a context for, mapped to the
    caller's membership row in it -- ``None`` where they see the pod without
    having joined it, which is what an organization owner does. Those get a
    context with no pod roles, which is correct: their authority comes from the
    org-owner shortcut in the authorizer, not from a membership.
    """
    if not pod_member_ids_by_pod:
        return {}

    principal_ids = [
        member_id
        for member_id in pod_member_ids_by_pod.values()
        if member_id is not None
    ]
    if organization_member_id is not None:
        principal_ids.append(organization_member_id)
    # ANY_POD, not a pod id: one query covers every pod on the page plus the
    # organization's own roles. It does no scoping, so every row is put back in
    # its place by `roles_applying_to_pod` below before it is merged -- the
    # organization member's rows reach every pod here, so the principal match
    # alone would not keep one pod's roles out of another's context.
    rows = await load_roles_for_principals(
        session,
        principal_ids=principal_ids,
        organization_id=organization_id,
        pod_scope=ANY_POD,
    )

    by_principal: dict[UUID, list[RoleRow]] = {}
    for row in rows:
        by_principal.setdefault(row[0], []).append(row)

    authorizer = Authorizer(session)
    contexts: dict[UUID, Context] = {}
    for pod_id, pod_member_id in pod_member_ids_by_pod.items():
        role_ids: set[UUID] = set()
        role_names: set[str] = set()
        permission_ids: set[str] = set()
        principal_refs: set[PrincipalRef] = set()

        if organization_member_id is not None:
            principal_refs.add(PrincipalRef("ORG_MEMBER", organization_member_id))
            merge_role_data(
                roles_applying_to_pod(
                    by_principal.get(organization_member_id, ()), pod_id
                ),
                role_ids,
                role_names,
                permission_ids,
            )
        if pod_member_id is not None:
            principal_refs.add(PrincipalRef("POD_MEMBER", pod_member_id))
            merge_role_data(
                roles_applying_to_pod(by_principal.get(pod_member_id, ()), pod_id),
                role_ids,
                role_names,
                permission_ids,
            )
        principal_refs.update(PrincipalRef("ROLE", role_id) for role_id in role_ids)

        frozen = frozenset(principal_refs)
        contexts[pod_id] = Context(
            actor_type=ActorType.USER,
            actor_id=str(user_id),
            user_id=user_id,
            organization_id=organization_id,
            pod_id=pod_id,
            role_ids=frozenset(role_ids),
            role_names=frozenset(role_names),
            permission_ids=frozenset(permission_ids),
            principal_refs=frozen,
            grant_principal_sets=(frozen,),
            authorizer=authorizer,
            request_id=request_id,
        )
    return contexts
