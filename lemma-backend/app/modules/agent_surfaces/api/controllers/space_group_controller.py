"""A pod's groups: the chats its bots are in, as one place in the pod.

Everything the Groups page does. It lists every group across the pod's
WhatsApp, Telegram and Slack surfaces; opens one with the people seen in it,
what is waiting on the reader and what was said there; starts a WhatsApp group
(a business number creates groups, it cannot join one); hands out the link that
adds the pod's Telegram bot to a group of the member's choosing; and changes
who answers the people outside the pod.

Reading takes what reading the group's bot takes, and every member of the pod
may read a group's page. Starting, linking and changing take what configuring
the bot takes -- the bar the in-chat setup of a channel sets.
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api.dependencies import CurrentUser, UoWDep, get_uow_factory
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.connectors.contracts.surfaces import account
from app.modules.agent_surfaces.api.dependencies import get_surface_service
from app.modules.agent_surfaces.api import group_public as public_api
from app.modules.agent_surfaces.services.group_owner_notes import tell_previous_owner
from app.modules.agent_surfaces.api.group_access import (
    GroupAccess,
)
from app.modules.agent_surfaces.api.surface_config_resolver import (
    require_surface_agent_action,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
)
from app.modules.agent_surfaces.services.space_groups import (
    GroupDetail,
    SpaceGroups,
)
from app.modules.agent_surfaces.services.surface_reach_resolver import (
    SurfaceReachResolver,
)
from app.modules.agent_surfaces.services.surface_service import AgentSurfaceService
from app.modules.agent_surfaces.services.telegram_group_links import (
    GroupLinkUnavailable,
    mint_group_link,
)
from app.modules.agent_surfaces.services.whatsapp_groups import (
    GroupNotOpened,
    GroupOpenLimitReached,
    GroupsNotAvailable,
    WhatsAppGroupOpener,
)

from app.modules.agent_surfaces.api.space_group_schemas import (
    GroupDetailResponse,
    GroupLineResponse,
    GroupLinkRequest,
    GroupLinkResponse,
    GroupListResponse,
    GroupResponse,
    GroupStartRequest,
    GroupTimelineResponse,
    GroupUpdateRequest,
    _detail_response,
    _response,
)

router = APIRouter(prefix="/pods/{pod_id}/groups", tags=["Agent Surfaces"])


async def _readable_group(
    *, pod_id: UUID, group_id: UUID, ctx, uow: UoWDep, viewer_id: UUID
) -> GroupDetail:
    detail = await SpaceGroups(uow).detail(
        pod_id=pod_id, group_id=group_id, viewer_id=viewer_id
    )
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    await _require_on_group(
        uow,
        ctx=ctx,
        pod_id=pod_id,
        surface_id=detail.group.surface_id,
        action=Permissions.AGENT_READ,
    )
    return detail


async def _surface(uow: UoWDep, surface_id: UUID) -> AgentSurfaceEntity:
    surface = await SpaceGroups(uow).surface(surface_id)
    if surface is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return surface


async def _bot_username(
    surface: AgentSurfaceEntity, *, service: AgentSurfaceService, uow: UoWDep
) -> str | None:
    """The bot's Telegram username, asked of Telegram if nobody has asked yet.

    A surface learns it the first time anybody asks how to reach it, so a bot
    connected a moment ago may not know it yet; this asks the same way.
    """
    if surface.surface_identity_username:
        return surface.surface_identity_username
    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=service.credential_resolver,
        find_account=partial(account, uow),
        surface_repository=service.surface_repository,
    )
    return reach.handle


async def _require_on_group(
    uow: UoWDep, *, ctx, pod_id: UUID, surface_id: UUID, action: str
) -> AgentSurfaceEntity:
    surface = await _surface(uow, surface_id)
    await require_surface_agent_action(
        ctx=ctx, pod_id=pod_id, agent_id=surface.agent_id, action=action
    )
    return surface


@router.get(
    "",
    operation_id="agent.group.list",
    response_model=GroupListResponse,
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_groups(
    pod_id: UUID, user: CurrentUser, ctx: PodContextDep, uow: UoWDep
) -> GroupListResponse:
    """Every group the pod's bots are in, most recently changed first."""
    summaries = await SpaceGroups(uow).summaries(pod_id=pod_id, viewer_id=user.id)
    access = GroupAccess(ctx=ctx, uow=uow, pod_id=pod_id, viewer_id=user.id)
    return GroupListResponse(
        items=[
            _response(summary, can_manage=await access.manages(summary))
            for summary in summaries
            if await access.reads(summary.group.surface_id)
        ]
    )


@router.post(
    "",
    operation_id="agent.group.start",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def start_group(
    pod_id: UUID,
    request: GroupStartRequest,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
    service: AgentSurfaceService = Depends(get_surface_service),
) -> GroupResponse:
    """Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    WhatsApp confirms the group moments later: it comes back ``pending``, and
    its invite link appears once confirmed. Telegram and Slack cannot create
    groups for a bot; add the bot to one of theirs instead.
    """
    surface = await service.get_surface_by_name_in_pod(
        pod_id=pod_id, name=request.surface_name
    )
    await require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_UPDATE,
    )
    if surface.surface_type is not SurfacePlatform.WHATSAPP:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only WhatsApp groups can be started from Lemma.",
        )
    # The opener keeps its own short sessions around the call to Meta.
    async with connection_released(uow.session):
        try:
            group, _ = await WhatsAppGroupOpener(
                uow_factory, wait_seconds=0
            ).request_group(
                surface_id=surface.id,
                owner_user_id=user.id,
                title=request.title,
                answers_outsiders=request.answers_outsiders,
            )
        except GroupOpenLimitReached as reached:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, detail=str(reached)
            ) from reached
        except GroupsNotAvailable as refused:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(refused)
            ) from refused
        except (GroupNotOpened, WhatsAppApiError, *platform_transport_errors()) as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="WhatsApp would not start the group. Try again shortly.",
            ) from exc
    detail = await SpaceGroups(uow).detail(
        pod_id=pod_id, group_id=group.id, viewer_id=user.id
    )
    if detail is None:  # pragma: no cover - written just above
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return _response(detail, can_manage=True)


@router.post(
    "/links",
    operation_id="agent.group.link",
    response_model=GroupLinkResponse,
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def group_link(
    pod_id: UUID,
    request: GroupLinkRequest,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> GroupLinkResponse:
    """A one-use link that adds the pod's Telegram bot to a group the caller picks.

    The group is then the caller's to answer for. Which Telegram account is
    theirs is still their profile's to say: a link is easily passed on, so the
    one that used it is never taken for them. The link works for an hour.
    """
    surface = await service.get_surface_by_name_in_pod(
        pod_id=pod_id, name=request.surface_name
    )
    await require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_UPDATE,
    )
    if surface.surface_type is not SurfacePlatform.TELEGRAM:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Add-to-group links are for Telegram.",
        )
    bot_username = await _bot_username(surface, service=service, uow=uow)
    try:
        async with connection_released(uow.session):
            url, expires_at = await mint_group_link(
                surface_id=surface.id, user_id=user.id, bot_username=bot_username
            )
    except GroupLinkUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return GroupLinkResponse(url=url, expires_at=expires_at)


@router.get(
    "/{group_id}",
    operation_id="agent.group.get",
    response_model=GroupDetailResponse,
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_group(
    pod_id: UUID, group_id: UUID, user: CurrentUser, ctx: PodContextDep, uow: UoWDep
) -> GroupDetailResponse:
    """One group: who is in it, and what its outsiders are waiting on you for."""
    detail = await _readable_group(
        pod_id=pod_id, group_id=group_id, ctx=ctx, uow=uow, viewer_id=user.id
    )
    access = GroupAccess(ctx=ctx, uow=uow, pod_id=pod_id, viewer_id=user.id)
    response = _detail_response(detail, can_manage=await access.manages(detail))
    response.public = await public_api.group_public(uow, pod_id=pod_id, ctx=ctx)
    return response


@router.get(
    "/{group_id}/timeline",
    operation_id="agent.group.timeline",
    response_model=GroupTimelineResponse,
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def group_timeline(
    pod_id: UUID,
    group_id: UUID,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
    limit: int = Query(default=60, ge=1, le=200),
) -> GroupTimelineResponse:
    """What was said in the group, oldest first, as far as the pod kept it.

    The group's channel timeline, kept for WhatsApp and Telegram groups. A
    Slack channel's history is Slack's; it comes back empty here. An answer the bot made with one member's
    own access comes back withheld to everybody else: they may not be able to
    see what it was made from.
    """
    group = await SpaceGroups(uow).group_in_pod(pod_id=pod_id, group_id=group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    await _require_on_group(
        uow,
        ctx=ctx,
        pod_id=pod_id,
        surface_id=group.surface_id,
        action=Permissions.AGENT_READ,
    )
    lines = await SpaceGroups(uow).timeline(
        pod_id=pod_id, group_id=group_id, viewer_id=user.id, limit=limit
    )
    return GroupTimelineResponse(
        items=[
            GroupLineResponse(
                author_name=item.author_name,
                author_external_id=item.author_key,
                in_pod=item.in_pod,
                from_bot=item.from_agent,
                text=None if item.withheld else item.text,
                withheld=item.withheld,
                at=item.created_at,
                answered_name=item.answered_name,
                answered_from_public=item.answered_from_public,
            )
            for item in lines or []
        ]
    )


@router.patch(
    "/{group_id}",
    operation_id="agent.group.update",
    response_model=GroupResponse,
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def update_group(
    pod_id: UUID,
    group_id: UUID,
    request: GroupUpdateRequest,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
) -> GroupResponse:
    """Switch outsiders on or off in one group, or take it over.

    The member who answers for the group may; so may anybody who configures the
    bot when nobody in the pod answers for it, and an admin of the pod, whose
    change the member is told about.
    """
    spaces = SpaceGroups(uow)
    current = await spaces.detail(pod_id=pod_id, group_id=group_id, viewer_id=user.id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    group = current.group
    await _require_on_group(
        uow,
        ctx=ctx,
        pod_id=pod_id,
        surface_id=group.surface_id,
        action=Permissions.AGENT_UPDATE,
    )
    access = GroupAccess(ctx=ctx, uow=uow, pod_id=pod_id, viewer_id=user.id)
    if not await access.manages(current):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the person who answers for this group, or an admin of the "
                "space, can change it."
            ),
        )
    takes_over = request.take_over or (
        bool(request.answers_outsiders) and not current.owner_in_pod
    )
    await spaces.change(
        group.id,
        new_owner=user.id if takes_over else None,
        answers_outsiders=request.answers_outsiders,
    )
    if (
        current.owner_in_pod
        and group.owner_user_id not in (None, user.id)
        and (takes_over or request.answers_outsiders is not None)
    ):
        await tell_previous_owner(
            uow,
            pod_id=pod_id,
            previous_owner=group.owner_user_id,
            actor_id=user.id,
            group=group,
            what_changed=_what_changed(request, takes_over=takes_over),
        )
    detail = await spaces.detail(pod_id=pod_id, group_id=group.id, viewer_id=user.id)
    if detail is None:  # pragma: no cover - read back inside one transaction
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return _response(detail, can_manage=True)


def _what_changed(request: GroupUpdateRequest, *, takes_over: bool) -> str:
    if takes_over:
        return "now answers for the people outside the space"
    if request.answers_outsiders:
        return "switched answering people outside the space on"
    return "switched answering people outside the space off"
