"""A group source's settings, and the two ways a member brings the bot into one.

A WhatsApp or Telegram group, a Slack or Teams channel, a group DM: each is a
source of one of the pod's GROUP channels, read and written in like any channel
(``/pods/{pod_id}/channels``). What is here is the bot's side of it, under the
channel it feeds: whether people outside the pod are answered there and which
member answers for them (``/channels/{c}/sources/{s}``); starting a WhatsApp
group (``/channels/whatsapp-groups`` -- a business number creates groups, it
cannot join one); and the link that adds the pod's Telegram bot to a group of
the member's choosing (``/channels/telegram-links``). The bot's settings page
lists its groups with ``GET /channels?source_surface=``, through the lister
registered here.

Here rather than in ``channels`` because it talks to the platforms. Its router
is included before the channels module's, so the literal segments are matched
before ``/channels/{channel_id}``.

Reading takes what reading the group's bot takes, and seeing the channel (or
managing the pod's channels): a private group's title and invite link are its
people's. Starting, linking and changing take what configuring the bot takes
-- the bar the in-chat setup of a channel sets.
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep, get_uow_factory
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.api import group_public as public_api
from app.modules.agent_surfaces.api.dependencies import get_surface_service
from app.modules.agent_surfaces.api.group_access import GroupAccess
from app.modules.agent_surfaces.api.source_schemas import (
    SourceSettingsDetailResponse,
    SourceUpdateRequest,
    TelegramLinkRequest,
    TelegramLinkResponse,
    WhatsAppGroupRequest,
    detail_response,
    settings_response,
)
from app.modules.agent_surfaces.api.surface_config_resolver import (
    may_perform_surface_agent_action,
    require_surface_agent_action,
)
from app.modules.agent_surfaces.composition import build_surface_service
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.whatsapp.client import WhatsAppApiError
from app.modules.agent_surfaces.services import group_start_keys
from app.modules.agent_surfaces.services.group_owner_notes import tell_previous_owner
from app.modules.agent_surfaces.services.source_settings import (
    SourceSettings,
    SourceState,
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
from app.modules.channels.contracts.lines import viewer_of, visible_channel
from app.modules.channels.contracts.source_settings import (
    SourceSettingsQuery,
    SourceSettingsResponse,
    lists_source_settings,
)
from app.modules.connectors.contracts.surfaces import account

router = APIRouter(prefix="/pods/{pod_id}/channels", tags=["Channel Sources"])

_NOT_FOUND = "Source not found"


async def _source(
    uow: UoWDep, *, pod_id: UUID, source_id: UUID, channel_id: UUID | None = None
) -> SourceState:
    """The pod's group source -- of this channel, when one is named."""
    state = await SourceSettings(uow).of(pod_id=pod_id, source_id=source_id)
    if state is None or (
        channel_id is not None and state.group.channel_id != channel_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return state


async def _seen_by(
    uow: UoWDep, ctx, *, pod_id: UUID, user_id: UUID, channel_id: UUID
) -> None:
    """404 unless the caller may see the source's channel, or manages the pod's
    channels -- a private group's title and invite link are its people's. A
    pod admin finds a group nobody let them into here, to join it."""
    if await ctx.can(Permissions.CHANNEL_MANAGE, ResourceRef.pod(pod_id)):
        return
    viewer = await viewer_of(uow, pod_id=pod_id, user_id=user_id)
    if await visible_channel(uow, viewer=viewer, channel_id=channel_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


async def _on_bot(ctx, *, pod_id: UUID, state: SourceState, action: str) -> None:
    await require_surface_agent_action(
        ctx=ctx, pod_id=pod_id, agent_id=state.agent_id, action=action
    )


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


async def _surface_for(
    service: AgentSurfaceService, ctx, *, pod_id: UUID, name: str
) -> AgentSurfaceEntity:
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=name)
    await require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_UPDATE,
    )
    return surface


@lists_source_settings
async def list_source_settings(
    query: SourceSettingsQuery,
) -> list[SourceSettingsResponse]:
    """Every group one bot is in, with each one's settings, newest first --
    for ``GET /channels?source_surface=``."""
    service = build_surface_service(query.uow)
    surface = await service.get_surface_by_name_in_pod(
        pod_id=query.pod_id, name=query.surface_name
    )
    if not await may_perform_surface_agent_action(
        ctx=query.ctx,
        pod_id=query.pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_READ,
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Reading this bot is not yours to do"
        )
    access = GroupAccess(
        ctx=query.ctx, uow=query.uow, pod_id=query.pod_id, viewer_id=query.viewer_id
    )
    states = await SourceSettings(query.uow).of_surface(
        pod_id=query.pod_id, surface=surface
    )
    return [
        settings_response(state, can_manage=await access.manages(state))
        for state in states
    ]


@router.post(
    "/whatsapp-groups",
    operation_id="channel.whatsapp_group.start",
    response_model=SourceSettingsResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
    responses={
        409: {"description": "A start with that Idempotency-Key is still running."},
        429: {"description": "The member has started as many groups as a day allows."},
    },
)
async def start_whatsapp_group(
    pod_id: UUID,
    request: WhatsAppGroupRequest,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
    service: AgentSurfaceService = Depends(get_surface_service),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=255
    ),
) -> SourceSettingsResponse:
    """Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    It is a new GROUP channel's source: the channel is made with it, and its
    id is `channel_id`. WhatsApp confirms the group moments later: it comes
    back ``pending``, and its invite link appears once confirmed. Telegram and
    Slack cannot create groups for a bot; add the bot to one of theirs instead.
    The same `Idempotency-Key` from the same member answers with the group it
    started, for a day.
    """
    surface = await _surface_for(service, ctx, pod_id=pod_id, name=request.surface_name)
    if surface.surface_type is not SurfacePlatform.WHATSAPP:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only WhatsApp groups can be started from Lemma.",
        )
    async with connection_released(uow.session):
        keyed = await group_start_keys.claim(
            pod_id=pod_id, user_id=user.id, key=idempotency_key
        )
    if keyed.started is not None:
        state = await _source(uow, pod_id=pod_id, source_id=keyed.started)
        return settings_response(state, can_manage=True)
    if keyed.in_progress:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="A start with that Idempotency-Key is still running.",
        )
    # The opener keeps its own short sessions around the call to Meta.
    async with connection_released(uow.session):
        try:
            group = await _open(
                uow_factory, surface=surface, user_id=user.id, request=request
            )
        except Exception:
            await group_start_keys.release(keyed)
            raise
        await group_start_keys.started(keyed, group.id)
    state = await _source(uow, pod_id=pod_id, source_id=group.id)
    return settings_response(state, can_manage=True)


async def _open(
    uow_factory: UnitOfWorkFactory,
    *,
    surface: AgentSurfaceEntity,
    user_id: UUID,
    request: WhatsAppGroupRequest,
) -> SurfaceGroup:
    """Ask Meta for the group; its refusals as the API says them."""
    try:
        group, _ = await WhatsAppGroupOpener(uow_factory, wait_seconds=0).request_group(
            surface_id=surface.id,
            owner_user_id=user_id,
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
    except WhatsAppApiError as exc:
        if exc.status_code == 429 or exc.status_code >= 500:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="WhatsApp would not start the group. Try again shortly.",
            ) from exc
        # Meta's refusal stands however often it is asked.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "WhatsApp refused the bot's credentials. Reconnect its account, "
                "then start the group again."
                if exc.status_code == 401
                else "WhatsApp refused to start the group. Trying again will not "
                "help until the bot's WhatsApp setup is fixed."
            ),
        ) from exc
    except (GroupNotOpened, *platform_transport_errors()) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="WhatsApp would not start the group. Try again shortly.",
        ) from exc
    return group


@router.post(
    "/telegram-links",
    operation_id="channel.telegram_link.create",
    response_model=TelegramLinkResponse,
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def telegram_link(
    pod_id: UUID,
    request: TelegramLinkRequest,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> TelegramLinkResponse:
    """A one-use link that adds the pod's Telegram bot to a group the caller picks.

    The group becomes a GROUP channel, the caller's to answer for. Which
    Telegram account is theirs is still their profile's to say: a link is
    easily passed on, so the one that used it is never taken for them. The link
    works for an hour.
    """
    surface = await _surface_for(service, ctx, pod_id=pod_id, name=request.surface_name)
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
    return TelegramLinkResponse(url=url, expires_at=expires_at)


@router.get(
    "/{channel_id}/sources/{source_id}",
    operation_id="channel.source.get",
    response_model=SourceSettingsDetailResponse,
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_source(
    pod_id: UUID,
    channel_id: UUID,
    source_id: UUID,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
) -> SourceSettingsDetailResponse:
    """A group source's settings: who answers its outsiders, and from what."""
    state = await _source(
        uow, pod_id=pod_id, source_id=source_id, channel_id=channel_id
    )
    await _seen_by(uow, ctx, pod_id=pod_id, user_id=user.id, channel_id=channel_id)
    await _on_bot(ctx, pod_id=pod_id, state=state, action=Permissions.AGENT_READ)
    access = GroupAccess(ctx=ctx, uow=uow, pod_id=pod_id, viewer_id=user.id)
    return detail_response(
        state,
        can_manage=await access.manages(state),
        public=await public_api.group_public(uow, pod_id=pod_id, ctx=ctx),
    )


@router.patch(
    "/{channel_id}/sources/{source_id}",
    operation_id="channel.source.update",
    response_model=SourceSettingsResponse,
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def update_source(
    pod_id: UUID,
    channel_id: UUID,
    source_id: UUID,
    request: SourceUpdateRequest,
    user: CurrentUser,
    ctx: PodContextDep,
    uow: UoWDep,
) -> SourceSettingsResponse:
    """Switch outsiders on or off in one group, or take it over (`owner: "me"`).
    Absent is unchanged; neither can be cleared.

    The member who answers for the group may; so may anybody who configures the
    bot when nobody in the pod answers for it, and an admin of the pod, whose
    change the member is told about.
    """
    named = set(request.model_fields_set)
    cleared = sorted(field for field in named if getattr(request, field) is None)
    if cleared:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot clear {', '.join(cleared)}",
        )
    current = await _source(
        uow, pod_id=pod_id, source_id=source_id, channel_id=channel_id
    )
    await _seen_by(uow, ctx, pod_id=pod_id, user_id=user.id, channel_id=channel_id)
    await _on_bot(ctx, pod_id=pod_id, state=current, action=Permissions.AGENT_UPDATE)
    access = GroupAccess(ctx=ctx, uow=uow, pod_id=pod_id, viewer_id=user.id)
    if not await access.manages(current):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the person who answers for this group, or an admin of the "
                "space, can change it."
            ),
        )
    group = current.group
    takes_over = request.owner == "me" or (
        bool(request.answers_outsiders) and not current.owner_in_pod
    )
    await SourceSettings(uow).change(
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
    state = await _source(uow, pod_id=pod_id, source_id=group.id)
    return settings_response(state, can_manage=True)


def _what_changed(request: SourceUpdateRequest, *, takes_over: bool) -> str:
    if takes_over:
        return "now answers for the people outside the space"
    if request.answers_outsiders:
        return "switched answering people outside the space on"
    return "switched answering people outside the space off"
