from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.composition.surface_agent import AgentServiceDep
from app.modules.agent_surfaces.api.controllers.surface_controller import (
    _require_surface_agent_action,
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
from app.modules.pod.infrastructure.pod_repositories import PodRepository

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
    agent_service: AgentServiceDep,
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

    agent = (
        await agent_service.get_agent_by_name(
            pod_id=pod_id,
            name=request.default_agent_name,
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

    pod_repository = PodRepository(uow)
    organization_id = await pod_repository.get_organization_id(pod_id)
    pod_name = await pod_repository.get_name(pod_id)
    if organization_id is None or pod_name is None:
        raise ValueError(f"Pod {pod_id} not found")

    setup = await service.start_setup(
        user_id=user.id,
        organization_id=organization_id,
        pod_id=pod_id,
        surface_name=surface_name,
        agent_id=agent.id if agent else None,
        surface_config=surface_config_from_input(
            request.config,
            channel_routes=[],
        ),
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
