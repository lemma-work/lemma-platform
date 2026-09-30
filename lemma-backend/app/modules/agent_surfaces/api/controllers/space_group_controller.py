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

from datetime import datetime
from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep, get_uow_factory
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.connectors.contracts.surfaces import account
from app.modules.agent_surfaces.api.dependencies import get_surface_service
from app.modules.agent_surfaces.api.surface_config_resolver import (
    may_perform_surface_agent_action,
    require_surface_agent_action,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    GROUP_SUBJECT_MAX_CHARS,
    WhatsAppApiError,
)
from app.modules.agent_surfaces.services.space_groups import (
    GroupDetail,
    GroupSummary,
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
    WhatsAppGroupOpener,
)

router = APIRouter(prefix="/pods/{pod_id}/groups", tags=["Agent Surfaces"])


class GroupOwnerResponse(BaseModel):
    user_id: UUID
    display_name: str | None = None


class GroupResponse(BaseModel):
    id: UUID
    surface_name: str
    platform: str
    title: str | None = None
    external_channel_id: str | None = None
    invite_link: str | None = None
    pending: bool = Field(
        default=False,
        description="Asked of the platform and not yet confirmed (WhatsApp).",
    )
    shared_externally: bool = Field(
        default=False,
        description="A Slack channel shared with another company.",
    )
    owner: GroupOwnerResponse | None = Field(
        default=None, description="Who answers for the people outside the pod."
    )
    answers_outsiders: bool
    welcomes_outsiders: bool = Field(
        description="Switched on, and somebody answers for them."
    )
    people_in_pod: int | None = Field(
        default=None,
        description="People in the pod seen speaking here; none where not kept.",
    )
    people_outside: int | None = Field(
        default=None,
        description="People outside the pod seen speaking here.",
    )
    last_message_at: datetime | None = None
    waiting_for_you: int = Field(
        default=0,
        description="Questions its people outside the pod passed on to you.",
    )
    updated_at: datetime


class GroupPersonResponse(BaseModel):
    name: str
    external_id: str | None = None
    user_id: UUID | None = None
    in_pod: bool


class GroupWaitingResponse(BaseModel):
    notification_id: UUID = Field(
        description="Answer it with the notification's respond endpoint."
    )
    question: str
    asked_at: datetime


class GroupDetailResponse(GroupResponse):
    people: list[GroupPersonResponse]
    waiting: list[GroupWaitingResponse]


class GroupListResponse(BaseModel):
    items: list[GroupResponse]


class GroupLineResponse(BaseModel):
    author_name: str | None = None
    author_external_id: str | None = None
    in_pod: bool
    from_bot: bool
    text: str
    at: datetime
    answered_name: str | None = Field(
        default=None, description="On the bot's lines: whom it answered."
    )
    answered_from_public: bool = Field(
        default=False,
        description="On the bot's lines: answered from what the pod made Public.",
    )


class GroupTimelineResponse(BaseModel):
    items: list[GroupLineResponse]


class GroupStartRequest(BaseModel):
    surface_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=GROUP_SUBJECT_MAX_CHARS)
    answers_outsiders: bool = True


class GroupUpdateRequest(BaseModel):
    answers_outsiders: bool | None = Field(
        default=None,
        description=(
            "Answer people outside the pod in this group. Switching it on where "
            "nobody answers for them makes the caller the one who does."
        ),
    )
    take_over: bool = Field(
        default=False,
        description="The caller answers for this group's outsiders from now on.",
    )


class GroupLinkRequest(BaseModel):
    surface_name: str = Field(min_length=1)


class GroupLinkResponse(BaseModel):
    url: str = Field(description="Opens Telegram's own choose-a-group screen.")
    expires_at: datetime


def _response(summary: GroupSummary) -> GroupResponse:
    group = summary.group
    owner = (
        GroupOwnerResponse(user_id=group.owner_user_id, display_name=summary.owner_name)
        if group.owner_user_id is not None
        else None
    )
    return GroupResponse(
        id=group.id,
        surface_name=summary.surface_name,
        platform=group.platform,
        title=group.title,
        external_channel_id=group.external_channel_id,
        invite_link=group.invite_link,
        pending=group.is_pending,
        shared_externally=group.shared_externally,
        owner=owner,
        answers_outsiders=group.answers_outsiders,
        welcomes_outsiders=group.welcomes_outsiders,
        people_in_pod=summary.members,
        people_outside=summary.outsiders,
        last_message_at=summary.last_message_at,
        waiting_for_you=summary.waiting_for_viewer,
        updated_at=group.updated_at,
    )


def _detail_response(detail: GroupDetail) -> GroupDetailResponse:
    return GroupDetailResponse(
        **_response(detail).model_dump(),
        people=[GroupPersonResponse(**p.model_dump()) for p in detail.people],
        waiting=[GroupWaitingResponse(**w.model_dump()) for w in detail.waiting],
    )


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
    from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (  # noqa: E501
        SurfaceRepository,
    )

    surface = await SurfaceRepository(uow).get(surface_id)
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
        credential_resolver=service._credential_resolver,
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
    readable: list[GroupResponse] = []
    agents: dict[UUID, bool] = {}
    for summary in summaries:
        surface_id = summary.group.surface_id
        if surface_id not in agents:
            surface = await _surface(uow, surface_id)
            agents[surface_id] = await may_perform_surface_agent_action(
                ctx=ctx,
                pod_id=pod_id,
                agent_id=surface.agent_id,
                action=Permissions.AGENT_READ,
            )
        if agents[surface_id]:
            readable.append(_response(summary))
    return GroupListResponse(items=readable)


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
        except (GroupNotOpened, WhatsAppApiError, *PLATFORM_TRANSPORT_ERRORS) as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="WhatsApp would not start the group. Try again shortly.",
            ) from exc
    detail = await SpaceGroups(uow).detail(
        pod_id=pod_id, group_id=group.id, viewer_id=user.id
    )
    if detail is None:  # pragma: no cover - written just above
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return _response(detail)


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
    return _detail_response(detail)


@router.get(
    "/{group_id}/timeline",
    operation_id="agent.group.timeline",
    response_model=GroupTimelineResponse,
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def group_timeline(
    pod_id: UUID,
    group_id: UUID,
    ctx: PodContextDep,
    uow: UoWDep,
    limit: int = Query(default=60, ge=1, le=200),
) -> GroupTimelineResponse:
    """What was said in the group, oldest first, as far as the pod kept it.

    Kept for WhatsApp and Telegram groups. A Slack channel's history is
    Slack's; it comes back empty here.
    """
    lines = await SpaceGroups(uow).timeline(
        pod_id=pod_id, group_id=group_id, limit=limit
    )
    if lines is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    group = await SurfaceGroupRepository(uow.session).get_by_id(group_id)
    if group is not None:
        await _require_on_group(
            uow,
            ctx=ctx,
            pod_id=pod_id,
            surface_id=group.surface_id,
            action=Permissions.AGENT_READ,
        )
    return GroupTimelineResponse(
        items=[
            GroupLineResponse(
                author_name=item.line.author_name,
                author_external_id=item.line.author_external_id,
                in_pod=item.in_pod,
                from_bot=item.line.from_agent,
                text=item.line.text,
                at=item.line.created_at,
                answered_name=item.line.answered_name,
                answered_from_public=item.line.answered_from_public,
            )
            for item in lines
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
    """Switch outsiders on or off in one group, or take it over."""
    groups = SurfaceGroupRepository(uow.session)
    group = await groups.get_by_id(group_id)
    if group is None or group.pod_id != pod_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    await _require_on_group(
        uow,
        ctx=ctx,
        pod_id=pod_id,
        surface_id=group.surface_id,
        action=Permissions.AGENT_UPDATE,
    )
    if request.take_over or (request.answers_outsiders and group.owner_user_id is None):
        await groups.set_owner(group.id, user.id)
    if request.answers_outsiders is not None:
        await groups.set_answers_outsiders(
            group.id, answers_outsiders=request.answers_outsiders
        )
    detail = await SpaceGroups(uow).detail(
        pod_id=pod_id, group_id=group.id, viewer_id=user.id
    )
    if detail is None:  # pragma: no cover - read back inside one transaction
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return _response(detail)
