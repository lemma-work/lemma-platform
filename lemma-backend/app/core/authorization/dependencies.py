"""FastAPI dependencies for the central request context."""

from __future__ import annotations

import inspect
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api.dependencies import CurrentUser, UoWDep, get_uow_factory
from app.core.authorization.pod_liveness import (
    _assert_pod_is_live,
    _refuse_a_deleted_pod,
)
from app.core.authorization.context import ActorType, Context, ResourceRef, ResourceType
from app.core.authorization.current import set_current_context
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_NAME,
    DESTRUCTIVE_ACTIONS,
    is_pod_default_agent,
)
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory


def _is_default_pod_agent_claims(claims) -> bool:
    """Whether this token was minted for the pod's own assistant.

    The one place the answer comes from a token rather than a row: this runs on
    every delegated request, and ``is_pod_default_agent`` is deliberately free
    of I/O so it need not become a query.

    Both claims have to agree. The id arm alone would promote any workload
    whose id happened to equal its pod's, and what turns on this is whether a
    token acts with its user's permissions or only its own grants.
    """
    return (
        claims is not None
        and is_pod_default_agent(claims.actor_id, pod_id=claims.pod_id)
        and claims.actor_name in {None, DEFAULT_POD_AGENT_NAME}
    )


async def resolve_current_context(
    *,
    session: AsyncSession,
    request: Request,
    user_id: UUID,
) -> Context:
    """Build the request's authorization ``Context`` on ``session``.

    Pure resolution from the delegation claims (the path functions/pods take when
    they call an endpoint with a delegated token) or the plain user context. Does
    NOT consult/mutate ``request.state.ctx`` or bind the contextvar -- callers do
    that. Extracted from ``get_current_context`` so the same logic can run inside
    a short ``current_context_scope`` (release the pooled connection before slow
    non-DB work) instead of only via the request-scoped dependency.
    """
    claims = getattr(request.state, "delegation_claims", None)
    if claims is not None:
        return await AuthorizationDataService(
            session
        ).build_context_from_delegation_claims(
            user_id=user_id,
            claims=claims,
            request_id=request.headers.get("x-request-id"),
            is_default_pod_agent=_is_default_pod_agent_claims(claims),
        )
    return await AuthorizationDataService(session).build_user_context(
        user_id=user_id,
        request_id=request.headers.get("x-request-id"),
    )


async def get_current_context(
    request: Request,
    user: CurrentUser,
    uow: UoWDep,
) -> Context:
    existing = getattr(request.state, "ctx", None)
    if existing is not None and existing.user_id == user.id:
        set_current_context(existing)
        return existing
    ctx = await resolve_current_context(
        session=uow.session, request=request, user_id=user.id
    )
    request.state.ctx = ctx
    set_current_context(ctx)
    await _release_after_authorization(uow)
    return ctx


async def get_org_context(
    request: Request,
    user: CurrentUser,
    uow: UoWDep,
) -> Context:
    raw_org_id = (
        request.path_params.get("org_id")
        or request.path_params.get("organization_id")
        or request.query_params.get("organization_id")
    )
    if raw_org_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="organization_id is required",
        )
    org_id = UUID(str(raw_org_id))
    existing = getattr(request.state, "ctx", None)
    if (
        existing is not None
        and existing.user_id == user.id
        and existing.organization_id == org_id
    ):
        set_current_context(existing)
        return existing
    claims = getattr(request.state, "delegation_claims", None)
    if claims is not None:
        ctx = await AuthorizationDataService(
            uow.session
        ).build_context_from_delegation_claims(
            user_id=user.id,
            claims=claims,
            request_id=request.headers.get("x-request-id"),
            is_default_pod_agent=_is_default_pod_agent_claims(claims),
        )
        if ctx.organization_id != org_id:
            raise HTTPException(
                status_code=403, detail="Delegated organization mismatch"
            )
        request.state.ctx = ctx
        set_current_context(ctx)
        await _release_after_authorization(uow)
        return ctx
    ctx = await AuthorizationDataService(uow.session).build_user_context(
        user_id=user.id,
        organization_id=org_id,
        request_id=request.headers.get("x-request-id"),
    )
    request.state.ctx = ctx
    set_current_context(ctx)
    await _release_after_authorization(uow)
    return ctx


async def resolve_pod_context(
    *,
    session: AsyncSession,
    request: Request,
    user_id: UUID,
    pod_id: UUID,
) -> Context:
    """Build the pod authorization context on a caller-provided session.

    Extracted from ``get_pod_context`` so streaming endpoints can build the
    context inside a SHORT unit of work (released before the StreamingResponse
    body) instead of holding the request-scoped ``UoWDep`` connection for the
    entire stream. The returned Context's authorizer is bound to ``session``, so
    it must only be used while that session is open.
    """
    claims = getattr(request.state, "delegation_claims", None)
    if claims is not None:
        if claims.pod_id != pod_id:
            raise HTTPException(status_code=403, detail="Delegated pod mismatch")
        return await AuthorizationDataService(
            session
        ).build_context_from_delegation_claims(
            user_id=user_id,
            claims=claims,
            request_id=request.headers.get("x-request-id"),
            is_default_pod_agent=_is_default_pod_agent_claims(claims),
        )
    return await AuthorizationDataService(session).build_user_context(
        user_id=user_id,
        pod_id=pod_id,
        request_id=request.headers.get("x-request-id"),
    )


async def get_pod_context(
    request: Request,
    user: CurrentUser,
    uow: UoWDep,
) -> Context:
    raw_pod_id = request.path_params.get("pod_id") or request.query_params.get("pod_id")
    if raw_pod_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pod_id is required",
        )
    try:
        pod_id = UUID(str(raw_pod_id))
    except ValueError:
        # The case the `is None` check above does not cover. Every pod-scoped
        # route reaches this, so an unparseable path segment used to leave an
        # unhandled ValueError and answer 500 — a server error for what is
        # entirely a malformed request, and in debug a traceback with it.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pod_id must be a UUID",
        ) from None
    existing = getattr(request.state, "ctx", None)
    if (
        existing is not None
        and existing.user_id == user.id
        and existing.pod_id == pod_id
    ):
        set_current_context(existing)
        return existing
    ctx = await resolve_pod_context(
        session=uow.session, request=request, user_id=user.id, pod_id=pod_id
    )
    _refuse_a_deleted_pod(request, ctx)
    request.state.ctx = ctx
    set_current_context(ctx)
    await _release_after_authorization(uow)
    return ctx


#: The operations a deleted pod still answers, and why each one has to.
#:
#: `pod.delete` is the whole list. PS-POD-050 promises a retried deletion
#: reports success rather than failing on the second attempt, and it cannot do
#: that if addressing the pod is itself refused. Everything else about a
#: deleted pod -- its schedules, its agents, its records, its members -- is
#: gone as far as the API is concerned.
async def _release_after_authorization(uow) -> None:
    """Give the pooled connection back once the context is built.

    These are FastAPI yield-dependencies, so without this the connection they
    check out to read the pod, the org and the role snapshot stays checked out
    until the response body is finished — through the handler, through whatever
    the handler awaits, and through serialization.

    Measured on a real-sandbox e2e run: 59 holds attributed to
    ``build_user_context``, the worst 2.78 seconds of a connection checked out
    with the database asked nothing. The reads are done by this point and the
    handler opens its own transaction the moment it queries, so there is
    nothing to keep.
    """
    await uow.commit()


CurrentContextDep = Annotated[Context, Depends(get_current_context)]
OrgContextDep = Annotated[Context, Depends(get_org_context)]
PodContextDep = Annotated[Context, Depends(get_pod_context)]


def reject_delegated_workload(action_label: str):
    """Deny a delegated workload outright for an org-scoped destructive action.

    Use where there is no pod context to run the nuanced destructive gate (an
    explicit grant / session approval are pod-scoped). A workload has no
    business performing these org-level, ownership-based actions — deleting a
    user's connected account, etc. Humans are unaffected.
    """

    async def _dependency(ctx: OrgContextDep) -> None:
        if ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD:
            from app.core.domain.errors import DomainError

            raise DomainError(
                f"Delegated workloads may not {action_label}.",
                code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
                status_code=403,
            )

    return Depends(_dependency)


def reject_delegated_workload_anywhere(action_label: str):
    """Deny a delegated workload on a route that has no organization in its path.

    :func:`reject_delegated_workload` resolves ``OrgContextDep``, which needs an
    ``org_id`` path or query parameter -- so the organization routes addressed
    only by an invitation id (accept, revoke) could not use it, and those are
    exactly the ones that mint or withdraw membership.

    Reading the claims straight off the request is not a shortcut around the
    context: ``verify_auth`` parses a delegated token on *every* path, which is
    why a workload token reaches these routes at all. The same fact that lets it
    in is the one that turns it away.
    """

    async def _dependency(request: Request) -> None:
        if getattr(request.state, "delegation_claims", None) is None:
            return
        from app.core.domain.errors import DomainError

        raise DomainError(
            f"Delegated workloads may not {action_label}.",
            code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
            status_code=403,
        )

    return Depends(_dependency)


def reject_delegated_workload_pod(action_label: str):
    """Pod-scoped counterpart of :func:`reject_delegated_workload`.

    For pod-routed ownership actions that mint membership (approving a join
    request grants org/pod membership). There is no per-resource grant or session
    approval that should let a workload confer membership on someone, so deny it
    outright. Humans are unaffected.
    """

    async def _dependency(ctx: PodContextDep) -> None:
        if ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD:
            from app.core.domain.errors import DomainError

            raise DomainError(
                f"Delegated workloads may not {action_label}.",
                code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
                status_code=403,
            )

    return Depends(_dependency)


def assert_pod_membership(ctx: Context, action_label: str = "browse this pod") -> None:
    """The membership rule itself: pure, and never touches the database.

    Separate from the dependency so the streaming endpoints can apply it inside
    the short ``pod_context_scope`` they already open, instead of taking a
    request-scoped ``PodContextDep`` that would pin a pooled connection for the
    whole StreamingResponse (see ``app.core.authorization.scope``).
    """
    if ctx.actor_type != ActorType.USER or ctx.is_superuser:
        return
    if any(ref.type == "POD_MEMBER" for ref in ctx.principal_refs):
        return
    # Org owners hold authority over every pod in their organization without
    # necessarily having a membership row — the same shortcut
    # Authorizer._is_org_owner_of_pod applies.
    if "ORG_OWNER" in ctx.role_names:
        return
    from app.core.domain.errors import DomainError

    raise DomainError(
        f"You need access to this pod to {action_label}.",
        code="POD_MEMBERSHIP_REQUIRED",
        status_code=403,
    )


def require_pod_membership(
    action_label: str = "browse this pod", *, enumerates: bool = False
):
    """Gate access on real membership, independent of what is readable.

    Listing endpoints carry no permission dependency, and pod-scoped resource
    routes resolve through the visibility projection in ``sql_actions``. That
    was safe while every above-pod visibility still resolved through the
    caller's pod role. It no longer is: ORGANIZATION and PUBLIC project read
    actions for people who are not in the pod, which is right for opening a
    resource someone sent you by its own public address and wrong for reaching
    into the pod that holds it.

    So the two directions move apart deliberately: a resource's own public
    address widens, everything routed through ``/pods/{pod_id}`` does not.
    Holding one shared link must not become a directory of every other shared
    thing, and the shape of a pod (folder names, table names, how much is in
    there) is not public just because one document in it is.

    Apps make the distinction concrete. They default to PUBLIC visibility on
    purpose, and PUBLIC projects ``.read`` to *any* signed-in user -- so
    ``app.get`` and the asset routes handed a pod's apps to any stranger with
    an account (PS-PACK-031). The published shell stays reachable, because
    strangers reach it by host through ``/public/apps``, which never routes
    through a pod at all.

    Only human callers are gated for membership. Workload actors keep the
    grant-first projection this change never widened, so agents and functions
    are unaffected.

    ``enumerates`` additionally refuses a pod that has been deleted -- see
    :func:`_assert_pod_is_live`. Pass it on routes that list, and only those.
    """

    async def _dependency(
        ctx: PodContextDep,
        uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
    ) -> None:
        if enumerates:
            await _assert_pod_is_live(uow_factory, ctx.pod_id)
        assert_pod_membership(ctx, action_label)

    return Depends(_dependency)


async def pod_from_path(request: Request) -> ResourceRef:
    raw_pod_id = request.path_params.get("pod_id")
    if not raw_pod_id:
        raise HTTPException(status_code=400, detail="Missing pod_id path parameter")
    pod_id = UUID(str(raw_pod_id))
    return ResourceRef.pod(pod_id)


def require_action(
    permission_id: str,
    resource_resolver=pod_from_path,
):
    async def _dependency(
        request: Request,
        ctx: PodContextDep,
    ) -> None:
        resolved = resource_resolver(request)
        resource = await resolved if inspect.isawaitable(resolved) else resolved
        await ctx.require(permission_id, resource)

    return Depends(_dependency)


def require_resource_action(
    permission_id: str,
    *,
    resource_type: ResourceType,
    id_param: str | None = None,
    name_param: str | None = None,
):
    async def _dependency(
        request: Request,
        ctx: PodContextDep,
        uow: UoWDep,
    ) -> None:
        resource = await _resource_from_request(
            request=request,
            ctx=ctx,
            uow=uow,
            resource_type=resource_type,
            id_param=id_param,
            name_param=name_param,
        )
        await ctx.require(permission_id, resource)
        await _release_after_authorization(uow)

    return Depends(_dependency)


def require_resource_admin_or_creator(
    permission_id: str,
    *,
    resource_type: ResourceType,
    id_param: str | None = None,
    name_param: str | None = None,
):
    async def _dependency(
        request: Request,
        ctx: PodContextDep,
        uow: UoWDep,
    ) -> None:
        resource = await _resource_from_request(
            request=request,
            ctx=ctx,
            uow=uow,
            resource_type=resource_type,
            id_param=id_param,
            name_param=name_param,
        )
        if await ctx.can(permission_id, resource):
            await _release_after_authorization(uow)
            return
        # The creator shortcut lets a human who created a resource delete it
        # without the role permission. A delegated workload must NOT get it for
        # a destructive action: table/agent/etc. deletion is gated (explicit
        # grant or session approval), and a workload delegating for the creator
        # would otherwise bypass that. Fall through to ctx.require (the gate).
        workload_destructive = (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
            and permission_id in DESTRUCTIVE_ACTIONS
        )
        if (
            not workload_destructive
            and ctx.user_id is not None
            and resource.resource_id is not None
        ):
            creator_user_id = await AuthorizationDataService(
                uow.session
            ).get_resource_creator(
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
            )
            if creator_user_id == ctx.user_id:
                await _release_after_authorization(uow)
                return
        await ctx.require(permission_id, resource)
        await _release_after_authorization(uow)

    return Depends(_dependency)


async def _resource_from_request(
    *,
    request: Request,
    ctx: Context,
    uow: UoWDep,
    resource_type: ResourceType,
    id_param: str | None,
    name_param: str | None,
) -> ResourceRef:
    if ctx.pod_id is None:
        raise HTTPException(status_code=400, detail="pod_id is required")

    resource_id: UUID | None = None
    resource_name: str | None = None
    if id_param is not None:
        raw_resource_id = request.path_params.get(id_param)
        if raw_resource_id is None:
            raise HTTPException(
                status_code=400,
                detail=f"Missing {id_param} path parameter",
            )
        resource_id = UUID(str(raw_resource_id))
    elif name_param is not None:
        raw_resource_name = request.path_params.get(name_param)
        if raw_resource_name is None:
            raise HTTPException(
                status_code=400,
                detail=f"Missing {name_param} path parameter",
            )
        resource_name = str(raw_resource_name)
    else:
        raise ValueError("id_param or name_param is required")

    resource = await AuthorizationDataService(uow.session).resolve_resource_ref(
        resource_type=resource_type,
        pod_id=ctx.pod_id,
        resource_id=resource_id,
        resource_name=resource_name,
    )
    if resource is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return resource
