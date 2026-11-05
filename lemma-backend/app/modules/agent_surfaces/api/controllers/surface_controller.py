from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.authorization.delegation import is_pod_default_agent
from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.api.deprecation import note_deprecated_use
from app.modules.connectors.contracts.deprecations import (
    ACCOUNT_ADDRESSING_SUNSET,
    ACCOUNT_ID_REPLACEMENT,
)
from app.modules.connectors.contracts.pod_connectors import shared_connector_for
from app.core.api.pagination import parse_uuid_page_token
from app.modules.agent.contracts.agents import (
    agent_id_for_name,
    agent_name_for_id,
    agent_names_for_ids,
)
from app.modules.agent_surfaces.services.surface_connector import bound_account
from app.modules.agent_surfaces.api.dependencies import (
    SurfaceConnectionResolverDep,
    MemberReachDep,
    get_surface_service,
)
from app.modules.agent_surfaces.api.schemas import (
    AgentSurfaceListResponse,
    AvailableSurfaceChannelResponse,
    AvailableSurfaceChannelsResponse,
    SurfaceSendRequest,
    SurfaceSendResponse,
    SurfaceSetupResponse,
    SurfaceUpdateRequest,
)
from app.modules.agent_surfaces.domain.surface_representation import (
    AgentSurfaceResponse,
    SurfaceCreateRequest,
    SurfaceReach,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.contracts.provisioning import surface_response
from app.modules.agent_surfaces.contracts.provisioning import warned_surface_response
from app.modules.agent_surfaces.services.surface_problems import surface_problem
from app.modules.agent_surfaces.services.contact_keepers import contacts_warnings
from app.modules.agent_surfaces.services.surface_config_input import (
    merge_surface_config,
    resolve_surface_config,
)
from app.modules.agent_surfaces.api.surface_config_resolver import (
    require_surface_agent_action,
    surface_setup_for_reader,
)
from app.modules.agent_surfaces.services.surface_identity_claim import (
    create_surface_claiming_identity,
)
from app.modules.agent_surfaces.services.surface_reach_resolver import (
    SurfaceReachResolver,
)
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)

router = APIRouter(prefix="/pods/{pod_id}/surfaces", tags=["Agent Surfaces"])


async def _resolve_surface_reaches(
    surfaces: Sequence[AgentSurfaceEntity],
    *,
    service: AgentSurfaceService,
    uow,
) -> list[SurfaceReach]:
    """``reach`` for a page of surfaces, in one call rather than a gather of
    one call per surface -- resolving a reach reads and writes through ``uow``,
    and a session cannot serve two of those at once. See ``resolve_many``."""
    return await SurfaceReachResolver().resolve_many(
        surfaces,
        credential_resolver=service.credential_resolver,
        find_account=partial(bound_account, uow),
        surface_repository=service.surface_repository,
    )


async def _resolve_surface_reach(
    surface: AgentSurfaceEntity,
    *,
    service: AgentSurfaceService,
    uow,
) -> SurfaceReach | None:
    """``reach`` for one surface."""
    reaches = await _resolve_surface_reaches([surface], service=service, uow=uow)
    return reaches[0]


async def _resolve_agent_id_filter(
    *,
    session,
    pod_id: UUID,
    agent_name: str | None,
) -> UUID | None:
    """Resolve an optional ``agent_name`` list filter to an agent id."""
    if not agent_name:
        return None
    return await agent_id_for_name(session, pod_id=pod_id, name=agent_name)


async def _resolve_agent_display_name(session, agent_id: UUID | None) -> str | None:
    if agent_id is None:
        return None
    return await agent_name_for_id(session, agent_id)


@router.get(
    "",
    response_model=AgentSurfaceListResponse,
    operation_id="agent.surface.list",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_surfaces(
    pod_id: UUID,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
    connection_resolver: SurfaceConnectionResolverDep,
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
        session=uow.session,
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
    readable: list[AgentSurfaceEntity] = []
    named: set[UUID] = set()
    for surface in surfaces:
        # The assistant's surfaces stay visible to every pod member: it is
        # pod-scoped, so there is no per-agent grant to hold. Reading this as an
        # ordinary agent would silently drop the pod's own mailbox out of the
        # listing for anyone without a grant on it.
        if not is_pod_default_agent(surface.agent_id, pod_id=pod_id):
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
            named.add(surface.agent_id)
        readable.append(surface)

    # One lookup for the page's agents, and one for its reaches, rather than
    # one of each per row.
    agent_names = await agent_names_for_ids(uow.session, named)
    reaches = await _resolve_surface_reaches(readable, service=service, uow=uow)
    visible: list[tuple[AgentSurfaceEntity, str | None, SurfaceReach | None]] = [
        (
            surface,
            agent_names.get(surface.agent_id) if surface.agent_id in named else None,
            reach,
        )
        for surface, reach in zip(readable, reaches)
    ]

    # Resolved once for the page, after the per-agent filter, so the two queries
    # it costs cover only surfaces this caller can actually see.
    connections = await connection_resolver.for_surfaces(
        [surface for surface, _, _ in visible],
        pod_id=pod_id,
        viewer_user_id=user.id,
    )
    warnings = await contacts_warnings(uow, pod_id, *(row[0] for row in visible))
    items = [
        surface_response(
            surface,
            agent_name=resolved_agent_name,
            reach=reach,
            connection=connections.get(surface.id),
            contacts_warning=warnings.get(surface.id),
            problem=await surface_problem(surface),
        )
        for surface, resolved_agent_name, reach in visible
    ]
    return AgentSurfaceListResponse(
        items=items,
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


async def _pod_connector_from_account(
    request: SurfaceCreateRequest | SurfaceUpdateRequest,
    *,
    pod_id: UUID,
    uow: UoWDep,
    ctx: PodContextDep,
    http_request: Request,
    response: Response,
) -> None:
    """Translate a deprecated ``account_id`` into the SHARED connector it means."""
    if request.account_id is None or request.pod_connector is not None:
        return
    request.pod_connector = await shared_connector_for(
        uow, ctx, pod_id=pod_id, account_id=request.account_id
    )
    note_deprecated_use(
        http_request,
        response,
        use="agent.surface.account_id",
        replacement=ACCOUNT_ID_REPLACEMENT,
        sunset=ACCOUNT_ADDRESSING_SUNSET,
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
    uow: UoWDep,
    ctx: PodContextDep,
    connection_resolver: SurfaceConnectionResolverDep,
    http_request: Request,
    response: Response,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AgentSurfaceResponse:
    """Create a surface. ``name`` defaults to the lowercased platform and is the
    pod-unique handle the API addresses it by. A second surface of the same
    platform has to belong to a different agent: one agent reaches a platform in
    one place — one Slack app, one WhatsApp number, one Telegram bot."""
    agent_name_id = (
        await agent_id_for_name(
            uow.session, pod_id=pod_id, name=request.default_agent_name
        )
        if request.default_agent_name
        else None
    )
    # Naming no agent means the pod's own assistant, whose row id is the pod's.
    # A surface belongs to exactly one agent, so "unowned" is not a state it can
    # be created in -- it used to be, and that is what let one column mean both
    # "who answers here" and "whose bot is this".
    agent_id = agent_name_id or pod_id
    await require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=agent_id,
        action=Permissions.AGENT_UPDATE,
    )
    await _pod_connector_from_account(
        request,
        pod_id=pod_id,
        uow=uow,
        ctx=ctx,
        http_request=http_request,
        response=response,
    )

    config = await resolve_surface_config(
        uow=uow,
        pod_id=pod_id,
        platform=request.platform,
        config_input=request.config,
        ctx=ctx,
    )
    surface = await create_surface_claiming_identity(
        service,
        pod_id=pod_id,
        agent_id=agent_name_id,
        agent_name=request.default_agent_name or None,
        platform=request.platform,
        name=request.name,
        config=config,
        credential_mode=request.credential_mode,
        pod_connector_slug=request.pod_connector,
        ctx=ctx,
        # Sent or not, which the resolved config cannot tell: "off" is also
        # what an omitted policy resolves to.
        contacts_chosen="contacts" in request.config.model_fields_set,
    )
    if request.platform is SurfacePlatform.TELEGRAM:
        await service.sync_telegram_mini_app(surface)
    if not request.is_enabled:
        surface = await service.update_surface(
            surface_id=surface.id,
            is_active=False,
            ctx=ctx,
        )
    reach = await _resolve_surface_reach(surface, service=service, uow=uow)
    connection = await connection_resolver.for_surface(
        surface, pod_id=pod_id, viewer_user_id=user.id
    )
    return surface_response(
        surface,
        agent_name=request.default_agent_name or None,
        reach=reach,
        connection=connection,
    )


@router.get(
    "/{surface_name}",
    operation_id="agent.surface.get",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_surface(
    pod_id: UUID,
    surface_name: str,
    user: CurrentUser,
    uow: UoWDep,
    ctx: PodContextDep,
    connection_resolver: SurfaceConnectionResolverDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AgentSurfaceResponse:
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    await require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_READ,
    )
    agent_name = await _resolve_agent_display_name(uow.session, surface.agent_id)
    reach = await _resolve_surface_reach(surface, service=service, uow=uow)
    connection = await connection_resolver.for_surface(
        surface, pod_id=pod_id, viewer_user_id=user.id
    )
    return await warned_surface_response(
        uow, surface, agent_name=agent_name, reach=reach, connection=connection
    )


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
    uow: UoWDep,
    ctx: PodContextDep,
    connection_resolver: SurfaceConnectionResolverDep,
    http_request: Request,
    response: Response,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AgentSurfaceResponse:
    """Partially update a surface. Only fields present in the request are
    applied; the surface's platform and name are immutable.

    Passing ``pod_connector`` rebinds the surface to a different SHARED pod
    connector -- the repair when the account it runs as is withdrawn or
    expires. The caller must be able to use that connector and its account."""
    update_agent_id = "default_agent_name" in request.model_fields_set
    agent_name_id = (
        await agent_id_for_name(
            uow.session, pod_id=pod_id, name=request.default_agent_name
        )
        if request.default_agent_name
        else None
    )
    # Naming no agent means the pod's own assistant, whose row id is the pod's.
    # A surface belongs to exactly one agent, so "unowned" is not a state it can
    # be created in -- it used to be, and that is what let one column mean both
    # "who answers here" and "whose bot is this".
    agent_id = agent_name_id or pod_id
    existing = await service.get_surface_by_name_in_pod(
        pod_id=pod_id, name=surface_name
    )
    # The agent the surface answers as now, always: a patch that names no agent
    # still disables or rebinds this one. And the agent it moves to, if any.
    for checked_agent_id in {existing.agent_id, agent_id if update_agent_id else None}:
        await require_surface_agent_action(
            ctx=ctx,
            pod_id=pod_id,
            agent_id=checked_agent_id,
            action=Permissions.AGENT_UPDATE,
        )
    await _pod_connector_from_account(
        request,
        pod_id=pod_id,
        uow=uow,
        ctx=ctx,
        http_request=http_request,
        response=response,
    )
    config = await merge_surface_config(
        uow=uow,
        existing=existing.config,
        pod_id=pod_id,
        platform=existing.surface_type,
        config_input=request.config,
        ctx=ctx,
    )
    updated = await service.update_surface(
        surface_id=existing.id,
        # Clearing the name means handing the surface back to the pod's own
        # assistant, not leaving it ownerless -- a surface answers as exactly
        # one agent, and there is no "none" for it to be.
        agent_id=agent_id,
        update_agent_id=update_agent_id,
        config=config,
        credential_mode=(
            request.credential_mode
            if "credential_mode" in request.model_fields_set
            else None
        ),
        pod_connector_slug=request.pod_connector,
        is_active=(
            request.is_enabled if "is_enabled" in request.model_fields_set else None
        ),
        ctx=ctx,
    )
    if (
        existing.surface_type is SurfacePlatform.TELEGRAM
        and "telegram" in request.config.model_fields_set
    ):
        await service.sync_telegram_mini_app(updated)
    resolved_agent_name = (
        request.default_agent_name
        if agent_name_id
        else await _resolve_agent_display_name(uow.session, updated.agent_id)
    )
    reach = await _resolve_surface_reach(updated, service=service, uow=uow)
    connection = await connection_resolver.for_surface(
        updated, pod_id=pod_id, viewer_user_id=user.id
    )
    return await warned_surface_response(
        uow,
        updated,
        agent_name=resolved_agent_name,
        reach=reach,
        connection=connection,
    )


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
    await require_surface_agent_action(
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
    ctx: PodContextDep,
    reach: MemberReachDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> SurfaceSendResponse:
    """Proactively send a message to a pod member on this surface.

    For functions and workflows that message a member on one platform. Reuses
    the member's existing thread (bots can't cold-DM), and a 404 carries the
    reason they could not be reached."""
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    await require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_UPDATE,
    )
    undeliverable = await reach.send_to_member(
        surface=surface,
        user_id=request.user_id,
        message=request.message,
    )
    if undeliverable is not None:
        raise HTTPException(status_code=404, detail=undeliverable)
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
    ctx: PodContextDep,
    connection_resolver: SurfaceConnectionResolverDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> SurfaceSetupResponse:
    """Live setup state for an existing surface: static platform checklist plus
    webhook URL and admin-consent status. Readable with ``AGENT_READ``; the org's
    own shared secrets in it are not. For the pre-creation checklist (before any
    surface exists) use ``GET /pods/{pod_id}/surface-setup/{platform}``."""
    setup = await surface_setup_for_reader(
        service=service,
        ctx=ctx,
        connection_resolver=connection_resolver,
        pod_id=pod_id,
        surface_name=surface_name,
        viewer_user_id=user.id,
    )
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
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AvailableSurfaceChannelsResponse:
    """List the channels/groups this surface bot can be configured to respond in.

    Returns an empty list for platforms without an enumerable channel concept
    (Telegram groups, WhatsApp, email).
    """
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    await require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_READ,
    )
    channels = await service.list_channels(surface=surface)
    return AvailableSurfaceChannelsResponse(
        channels=[
            AvailableSurfaceChannelResponse(
                id=channel.id, name=channel.name, is_member=channel.is_member
            )
            for channel in channels
        ]
    )
