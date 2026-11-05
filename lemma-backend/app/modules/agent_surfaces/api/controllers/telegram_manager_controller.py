from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.modules.agent.contracts.agents import agent_id_for_name
from app.modules.agent_surfaces.api.surface_config_resolver import (
    require_surface_agent_action,
)
from app.modules.agent_surfaces.services.surface_config_input import (
    resolve_telegram_config,
)
from app.modules.agent_surfaces.api.dependencies import (
    SurfaceServiceDep,
    TelegramManagerServiceDep,
)
from app.modules.agent_surfaces.api.schemas import (
    TelegramManagedBotSetupRequest,
    TelegramManagedBotSetupResponse,
)
from app.modules.agent_surfaces.domain.surface_representation import (
    surface_config_from_input,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.pod_scope import pod_scope
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceAlreadyExistsError,
)

router = APIRouter(
    prefix="/pods/{pod_id}/telegram-bot-setups",
    tags=["Agent Surfaces"],
)


#: Statuses the sheet shows as an outcome -- the bot is live, or the reason it
#: is not -- and never with the link to the manager bot.
_FINISHED_STATUSES = frozenset({"COMPLETE", "FAILED"})


async def _response(
    service, setup, *, manager_username: str | None
) -> TelegramManagedBotSetupResponse:
    """One setup, as the sheet reads it.

    ``manager_username`` is what `TelegramManagerService.manager_username`
    answered, resolved once by the caller: it is both what the link points at
    and what the sheet names, and two answers there would send somebody to a
    bot the page did not name.

    Only a setup the person is still finishing in Telegram needs that link. A
    finished one is answered without it when no username can be found, so the
    person waiting sees their bot go live, or why it did not, rather than a 503.
    """
    launch_url = (
        ""
        if not manager_username and setup.status.value in _FINISHED_STATUSES
        else service.launch_url(setup, manager_username=manager_username)
    )
    return TelegramManagedBotSetupResponse(
        setup_id=setup.setup_id,
        status=setup.status.value,
        launch_url=launch_url,
        manager_bot_username=manager_username or "",
        expires_at=setup.expires_at.isoformat(),
        pod_connector=setup.pod_connector_slug,
        surface_id=setup.surface_id,
        bot_username=setup.bot_username,
        bot_launch_url=(
            await service.bot_launch_url(setup)
            if setup.status.value == "COMPLETE" and setup.bot_username
            else None
        ),
        error=setup.error,
    )


@router.post(
    "",
    operation_id="agent.surface.telegram_managed.start",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def start_telegram_managed_bot_setup(
    pod_id: UUID,
    request: TelegramManagedBotSetupRequest,
    user: CurrentUser,
    uow: UoWDep,
    ctx: PodContextDep,
    service: TelegramManagerServiceDep,
    surfaces: SurfaceServiceDep,
) -> TelegramManagedBotSetupResponse:
    surface_name = (request.name or "").strip() or AgentSurfaceEntity.default_name_for(
        SurfacePlatform.TELEGRAM
    )
    if await surfaces.surface_name_taken(pod_id=pod_id, name=surface_name):
        raise AgentSurfaceAlreadyExistsError(surface_name)

    agent_name_id = (
        await agent_id_for_name(uow, pod_id=pod_id, name=request.default_agent_name)
        if request.default_agent_name
        else None
    )
    # Naming no agent means the pod's own assistant, whose row id is the pod's
    # -- as on the plain create. Left None, the setup ran to the end and the
    # surface was refused at creation for having no owner, after Telegram had
    # already made the bot.
    agent_id = agent_name_id or pod_id
    await require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=agent_id,
        action=Permissions.AGENT_UPDATE,
    )

    organization_id, pod_name = await pod_scope(uow, pod_id)

    surface_config = surface_config_from_input(
        request.config,
        channel_routes=[],
    )
    surface_config.telegram = await resolve_telegram_config(
        uow=uow,
        pod_id=pod_id,
        platform=SurfacePlatform.TELEGRAM,
        app_name=request.config.telegram.app_name,
        ctx=ctx,
    )
    # Every read above is done, and what follows is Telegram and Redis only --
    # `start_setup`'s store (`TelegramManagedBotSetupStore`) speaks to Redis and
    # nothing else. So the pooled connection goes back before the username
    # lookup and the setup allocation, which retries up to five times against
    # Redis to find a free setup id.
    async with connection_released(uow.session):
        # Asked before the setup is claimed, and handed to `start_setup`, which
        # refuses without one rather than claim this surface for a setup that
        # has no bot to send anybody to.
        manager_username = await service.manager_username()
        setup = await service.start_setup(
            user_id=user.id,
            organization_id=organization_id,
            pod_id=pod_id,
            surface_name=surface_name,
            agent_id=agent_id,
            surface_config=surface_config,
            is_enabled=request.is_enabled,
            pod_name=pod_name,
            manager_username=manager_username,
        )
    return await _response(service, setup, manager_username=manager_username)


@router.get(
    "/{setup_id}",
    operation_id="agent.surface.telegram_managed.get",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_telegram_managed_bot_setup(
    pod_id: UUID,
    setup_id: str,
    user: CurrentUser,
    service: TelegramManagerServiceDep,
) -> TelegramManagedBotSetupResponse:
    setup = await service.get_setup(
        setup_id=setup_id,
        user_id=user.id,
        pod_id=pod_id,
    )
    return await _response(
        service, setup, manager_username=await service.manager_username()
    )
