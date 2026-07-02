"""FastAPI dependencies for the central request context."""

from __future__ import annotations

import inspect
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ActorType, Context, ResourceRef, ResourceType
from app.core.authorization.current import set_current_context
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
    DESTRUCTIVE_ACTIONS,
)
from app.core.authorization.service import AuthorizationDataService


def _is_default_pod_agent_claims(claims) -> bool:
    return (
        claims is not None
        and claims.actor_id == DEFAULT_POD_AGENT_ID
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
        return await AuthorizationDataService(session).build_context_from_delegation_claims(
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
    return ctx


async def get_org_context(
    request: Request,
    user: CurrentUser,
    uow: UoWDep,
) -> Context:
    raw_org_id = request.path_params.get("org_id") or request.query_params.get(
        "organization_id"
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
        ctx = await AuthorizationDataService(uow.session).build_context_from_delegation_claims(
            user_id=user.id,
            claims=claims,
            request_id=request.headers.get("x-request-id"),
            is_default_pod_agent=_is_default_pod_agent_claims(claims),
        )
        if ctx.organization_id != org_id:
            raise HTTPException(status_code=403, detail="Delegated organization mismatch")
        request.state.ctx = ctx
        set_current_context(ctx)
        return ctx
    ctx = await AuthorizationDataService(uow.session).build_user_context(
        user_id=user.id,
        organization_id=org_id,
        request_id=request.headers.get("x-request-id"),
    )
    request.state.ctx = ctx
    set_current_context(ctx)
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
    pod_id = UUID(str(raw_pod_id))
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
    request.state.ctx = ctx
    set_current_context(ctx)
    return ctx


CurrentContextDep = Annotated[Context, Depends(get_current_context)]
OrgContextDep = Annotated[Context, Depends(get_org_context)]
PodContextDep = Annotated[Context, Depends(get_pod_context)]


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
            creator_user_id = await AuthorizationDataService(uow.session).get_resource_creator(
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
            )
            if creator_user_id == ctx.user_id:
                return
        await ctx.require(permission_id, resource)

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
