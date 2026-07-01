from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.dependencies import require_action
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.permissions import Permissions
from app.core.api.dependencies import CurrentUser
from app.core.api.pagination import parse_uuid_page_token
from app.modules.agent.api.dependencies import AgentServiceDep
from app.modules.agent_surfaces.api.dependencies import (
    SurfaceEventHandlerDep,
    get_surface_service,
)
from app.modules.agent_surfaces.api.schemas import (
    AgentSurfaceListResponse,
    AgentSurfaceResponse,
    AvailableSurfaceChannelResponse,
    AvailableSurfaceChannelsResponse,
    SurfaceBehaviorConfigInput,
    SurfaceConfigResponse,
    SurfaceCreateRequest,
    SurfaceSendRequest,
    SurfaceSendResponse,
    SurfaceSetupResponse,
    SurfaceUpdateRequest,
    surface_config_from_input,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceIdentityPolicy,
    SurfacePlatform,
    SurfaceSendPolicy,
)
from app.modules.agent_surfaces.domain.setup_guides import SurfacePlatformSetupGuide
from app.modules.agent_surfaces.platforms.common import computed_webhook_url
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)

router = APIRouter(prefix="/pods/{pod_id}/surfaces", tags=["Agent Surfaces"])

# A surface's platform-level setup checklist (env/OAuth prerequisites) needs no
# surface to exist yet, so it lives outside the surface-resource router.
setup_guide_router = APIRouter(
    prefix="/pods/{pod_id}/surface-setup", tags=["Agent Surfaces"]
)


async def _require_surface_agent_action(
    *,
    ctx,
    pod_id: UUID,
    agent_id: UUID | None,
    action: str,
) -> None:
    if agent_id is None:
        return
    await ctx.require(
        action,
        ResourceRef(
            resource_type=ResourceType.AGENT,
            resource_id=agent_id,
            pod_id=pod_id,
        ),
    )


def _surface_response(
    surface: AgentSurfaceEntity,
    *,
    agent_name: str | None = None,
) -> AgentSurfaceResponse:
    return AgentSurfaceResponse(
        id=surface.id,
        pod_id=surface.pod_id,
        name=surface.name,
        agent_id=surface.agent_id,
        agent_name=agent_name,
        uses_default_agent=surface.agent_id is None,
        platform=surface.surface_type,
        credential_mode=surface.credential_mode,
        account_id=surface.account_id,
        surface_identity_id=surface.surface_identity_id,
        surface_identity_username=surface.surface_identity_username,
        webhook_url=computed_webhook_url(surface),
        config=SurfaceConfigResponse.from_domain(surface.config),
        status=surface.status,
    )


def _surface_platform_from_ref(platform: str) -> SurfacePlatform:
    try:
        return SurfacePlatform(str(platform).upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported surface platform: {platform}",
        ) from exc


async def _resolve_agent_id_filter(
    *,
    agent_service,
    pod_id: UUID,
    agent_name: str | None,
) -> UUID | None:
    """Resolve an optional ``agent_name`` list filter to an agent id."""
    if not agent_name:
        return None
    agent = await agent_service.get_agent_by_name(pod_id=pod_id, name=agent_name)
    return agent.id


async def _resolve_channel_routes(
    *,
    pod_id: UUID,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> list[SurfaceChannelRoute]:
    """Validate route agent names exist, enforcing per-agent permissions."""
    routes: list[SurfaceChannelRoute] = []
    for route in config_input.channels:
        agent_name = None
        if route.agent_name:
            agent = await agent_service.get_agent_by_name(
                pod_id=pod_id,
                name=route.agent_name,
            )
            await _require_surface_agent_action(
                ctx=ctx,
                pod_id=pod_id,
                agent_id=agent.id,
                action=Permissions.AGENT_UPDATE,
            )
            agent_name = agent.name
        routes.append(
            SurfaceChannelRoute(
                channel_id=route.channel_id,
                channel_name=route.channel_name,
                agent_name=agent_name,
            )
        )
    return routes


async def _resolve_surface_config(
    *,
    pod_id: UUID,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> SurfaceConfig:
    channel_routes = await _resolve_channel_routes(
        pod_id=pod_id,
        config_input=config_input,
        agent_service=agent_service,
        ctx=ctx,
    )
    return surface_config_from_input(config_input, channel_routes=channel_routes)


async def _merge_surface_config(
    *,
    existing: SurfaceConfig,
    pod_id: UUID,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> SurfaceConfig:
    """Apply only the fields the caller actually sent on top of the stored config."""
    updates: dict = {}
    if "identity" in config_input.model_fields_set:
        updates["identity"] = SurfaceIdentityPolicy(
            allowed_domains=config_input.identity.allowed_domains,
            allowed_email_addresses=config_input.identity.allowed_email_addresses,
        )
    if "channels" in config_input.model_fields_set:
        updates["channels"] = await _resolve_channel_routes(
            pod_id=pod_id,
            config_input=config_input,
            agent_service=agent_service,
            ctx=ctx,
        )
    if "dm_conversation_reset_after_hours" in config_input.model_fields_set:
        updates["dm_conversation_reset_after_hours"] = (
            config_input.dm_conversation_reset_after_hours
        )
    if "send_policy" in config_input.model_fields_set:
        updates["send_policy"] = SurfaceSendPolicy(
            allow_send=config_input.send_policy.allow_send
        )
    return existing.model_copy(update=updates)


async def _resolve_agent_display_name(agent_service, agent_id: UUID | None) -> str | None:
    if agent_id is None:
        return None
    try:
        agent = await agent_service.agent_repository.get(agent_id)
        return agent.name if agent else None
    except Exception:
        return None


@router.get(
    "",
    response_model=AgentSurfaceListResponse,
    operation_id="agent.surface.list",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_surfaces(
    pod_id: UUID,
    user: CurrentUser,
    agent_service: AgentServiceDep,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
    limit: int = 100,
    page_token: str | None = None,
    platform: str | None = None,
    agent_name: str | None = None,
) -> AgentSurfaceListResponse:
    """List surfaces in the pod. A pod may have several surfaces of the same
    ``platform`` (different bots/accounts, one per agent); filter by
    ``platform`` and/or ``agent_name`` to narrow the results."""
    cursor = parse_uuid_page_token(page_token)

    agent_id_filter = await _resolve_agent_id_filter(
        agent_service=agent_service,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    surfaces, next_cursor = await service.list_surfaces_by_pod(
        pod_id,
        platform=platform,
        agent_id=agent_id_filter,
        match_agent=agent_id_filter is not None,
        cursor=cursor,
        limit=limit,
    )
    items = []
    for surface in surfaces:
        resolved_agent_name = None
        if surface.agent_id is not None:
            allowed = await ctx.can(
                Permissions.AGENT_READ,
                ResourceRef(
                    resource_type=ResourceType.AGENT,
                    resource_id=surface.agent_id,
                    pod_id=pod_id,
                ),
            )
            if not allowed:
                continue
            resolved_agent_name = await _resolve_agent_display_name(
                agent_service, surface.agent_id
            )
        items.append(_surface_response(surface, agent_name=resolved_agent_name))
    return AgentSurfaceListResponse(
        items=items,
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.post(
    "",
    operation_id="agent.surface.create",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def create_surface(
    pod_id: UUID,
    request: SurfaceCreateRequest,
    user: CurrentUser,
    agent_service: AgentServiceDep,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AgentSurfaceResponse:
    """Create a surface. ``name`` defaults to the lowercased platform — pass an
    explicit name to create a second surface of the same platform (e.g. a
    second bot routed to a different agent)."""
    agent = (
        await agent_service.get_agent_by_name(
            pod_id=pod_id, name=request.default_agent_name
        )
        if request.default_agent_name
        else None
    )
    await _require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=agent.id if agent else None,
        action=Permissions.AGENT_UPDATE,
    )

    config = await _resolve_surface_config(
        pod_id=pod_id,
        config_input=request.config,
        agent_service=agent_service,
        ctx=ctx,
    )
    surface = await service.create_surface(
        pod_id=pod_id,
        agent_id=agent.id if agent else None,
        platform=request.platform,
        name=request.name,
        config=config,
        credential_mode=request.credential_mode,
        account_id=request.account_id,
        ctx=ctx,
    )
    if not request.is_enabled:
        surface = await service.update_surface(
            surface_id=surface.id,
            is_active=False,
            ctx=ctx,
        )
    del user
    return _surface_response(surface, agent_name=agent.name if agent else None)


@router.get(
    "/{surface_name}",
    operation_id="agent.surface.get",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_surface(
    pod_id: UUID,
    surface_name: str,
    user: CurrentUser,
    agent_service: AgentServiceDep,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AgentSurfaceResponse:
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    await _require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_READ,
    )
    agent_name = await _resolve_agent_display_name(agent_service, surface.agent_id)
    del user
    return _surface_response(surface, agent_name=agent_name)


@router.patch(
    "/{surface_name}",
    operation_id="agent.surface.update",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def update_surface(
    pod_id: UUID,
    surface_name: str,
    request: SurfaceUpdateRequest,
    user: CurrentUser,
    agent_service: AgentServiceDep,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AgentSurfaceResponse:
    """Partially update a surface. Only fields present in the request are
    applied; the surface's platform and name are immutable."""
    update_agent_id = "default_agent_name" in request.model_fields_set
    agent = (
        await agent_service.get_agent_by_name(
            pod_id=pod_id, name=request.default_agent_name
        )
        if request.default_agent_name
        else None
    )
    await _require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=agent.id if agent else None,
        action=Permissions.AGENT_UPDATE,
    )

    existing = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    config = await _merge_surface_config(
        existing=existing.config,
        pod_id=pod_id,
        config_input=request.config,
        agent_service=agent_service,
        ctx=ctx,
    )
    updated = await service.update_surface(
        surface_id=existing.id,
        agent_id=agent.id if agent else None,
        update_agent_id=update_agent_id,
        config=config,
        credential_mode=(
            request.credential_mode
            if "credential_mode" in request.model_fields_set
            else None
        ),
        account_id=request.account_id,
        is_active=(
            request.is_enabled
            if "is_enabled" in request.model_fields_set
            else None
        ),
        ctx=ctx,
    )
    del user
    resolved_agent_name = agent.name if agent else await _resolve_agent_display_name(
        agent_service, updated.agent_id
    )
    return _surface_response(updated, agent_name=resolved_agent_name)


@router.delete(
    "/{surface_name}",
    operation_id="agent.surface.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.AGENT_DELETE)],
)
async def delete_surface(
    pod_id: UUID,
    surface_name: str,
    user: CurrentUser,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
):
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    await _require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_DELETE,
    )
    await service.delete_surface(surface.id)
    del user
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{surface_name}/send",
    response_model=SurfaceSendResponse,
    operation_id="agent.surface.send",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def send_surface_message(
    pod_id: UUID,
    surface_name: str,
    request: SurfaceSendRequest,
    user: CurrentUser,
    ingress: SurfaceEventHandlerDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> SurfaceSendResponse:
    """Proactively send a message to a pod member on this surface.

    Powers notifications from functions/workflows. Reuses the member's existing
    thread on the surface (bots can't cold-DM), so a 404 means the member has no
    reachable conversation here yet.
    """
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    sent = await ingress.send_to_member(
        surface=surface,
        user_id=request.user_id,
        message=request.message,
    )
    if not sent:
        raise HTTPException(
            status_code=404,
            detail="Member has no reachable conversation on this surface.",
        )
    del user
    return SurfaceSendResponse(sent=True)


@router.get(
    "/{surface_name}/setup",
    response_model=SurfaceSetupResponse,
    operation_id="agent.surface.setup",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_surface_setup(
    pod_id: UUID,
    surface_name: str,
    user: CurrentUser,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> SurfaceSetupResponse:
    """Live setup state for an existing surface: static platform checklist plus
    webhook URL and admin-consent status. For the pre-creation checklist (before
    any surface exists) use ``GET /pods/{pod_id}/surface-setup/{platform}``."""
    del user
    setup = await service.get_surface_setup_by_name(pod_id=pod_id, name=surface_name)
    return SurfaceSetupResponse.model_validate(setup)


@router.get(
    "/{surface_name}/channels",
    operation_id="agent.surface.channels",
    response_model=AvailableSurfaceChannelsResponse,
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_surface_channels(
    pod_id: UUID,
    surface_name: str,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AvailableSurfaceChannelsResponse:
    """List the channels/groups this surface bot can be configured to respond in.

    Returns an empty list for platforms without an enumerable channel concept
    (Telegram groups, WhatsApp, email).
    """
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    channels = await service.list_channels(surface=surface)
    return AvailableSurfaceChannelsResponse(
        channels=[
            AvailableSurfaceChannelResponse(
                id=channel.id, name=channel.name, is_member=channel.is_member
            )
            for channel in channels
        ]
    )


@setup_guide_router.get(
    "/{platform}",
    operation_id="agent.surface.setup_guide",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_surface_setup_guide(
    pod_id: UUID,
    platform: str,
    user: CurrentUser,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> SurfacePlatformSetupGuide:
    """The static pre-creation checklist for a platform (env/OAuth
    prerequisites) — works before any surface of this platform exists."""
    del user, pod_id
    return service.get_platform_setup_guide(platform)
