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
    resolve_telegram_config,
)
from app.modules.agent_surfaces.api.dependencies import TelegramManagerServiceDep
from app.modules.agent_surfaces.api.schemas import (
    TelegramManagedBotSetupRequest,
    TelegramManagedBotSetupResponse,
    surface_config_from_input,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceAlreadyExistsError,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.pod.contracts.agent_access import pod_organization_id
from app.modules.pod.contracts.members import pod_name as pod_name_of

router = APIRouter(
    prefix="/pods/{pod_id}/telegram-bot-setups",
    tags=["Agent Surfaces"],
)


def _response(service, setup) -> TelegramManagedBotSetupResponse:
    return TelegramManagedBotSetupResponse(
        setup_id=setup.setup_id,
        status=setup.status.value,
        launch_url=service.launch_url(setup),
        manager_bot_username=service.manager_username,
        expires_at=setup.expires_at.isoformat(),
        account_id=setup.account_id,
        surface_id=setup.surface_id,
        bot_username=setup.bot_username,
        bot_launch_url=(
            service.bot_launch_url(setup)
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
) -> TelegramManagedBotSetupResponse:
    surface_name = (request.name or "").strip() or AgentSurfaceEntity.default_name_for(
        SurfacePlatform.TELEGRAM
    )
    existing = await SurfaceRepository(uow).get_by_pod_and_name(
        pod_id=pod_id,
        name=surface_name,
    )
    if existing is not None:
        raise AgentSurfaceAlreadyExistsError(surface_name)

    agent_name_id = (
        await agent_id_for_name(
            uow.session, pod_id=pod_id, name=request.default_agent_name
        )
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

    organization_id = await pod_organization_id(uow, pod_id)
    pod_name = await pod_name_of(uow.session, pod_id)
    if organization_id is None or pod_name is None:
        raise ValueError(f"Pod {pod_id} not found")

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
    # Every read above is done, and `start_setup` is Redis only -- its store
    # (`TelegramManagedBotSetupStore`) speaks to Redis and nothing else. So the
    # pooled connection goes back before the setup allocation, which retries up
    # to five times against Redis to find a free setup id.
    async with connection_released(uow.session):
        setup = await service.start_setup(
            user_id=user.id,
            organization_id=organization_id,
            pod_id=pod_id,
            surface_name=surface_name,
            agent_id=agent_id,
            surface_config=surface_config,
            is_enabled=request.is_enabled,
            pod_name=pod_name,
        )
    return _response(service, setup)


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
    return _response(service, setup)
