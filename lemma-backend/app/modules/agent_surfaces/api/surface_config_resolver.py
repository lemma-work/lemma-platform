from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.api.schemas import (
    SurfaceBehaviorConfigInput,
    surface_config_from_input,
)
from app.modules.agent_surfaces.domain.entities import (
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceIdentityPolicy,
    SurfacePlatform,
    SurfaceSendPolicy,
    SurfaceTelegramConfig,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.apps.contracts import get_ready_pod_app_by_name


async def require_surface_agent_action(
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


async def _resolve_channel_routes(
    *,
    pod_id: UUID,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> list[SurfaceChannelRoute]:
    routes: list[SurfaceChannelRoute] = []
    for route in config_input.channels:
        agent_name = None
        if route.agent_name:
            agent = await agent_service.get_agent_by_name(
                pod_id=pod_id,
                name=route.agent_name,
            )
            await require_surface_agent_action(
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


async def resolve_telegram_config(
    *,
    uow,
    pod_id: UUID,
    platform: SurfacePlatform,
    app_name: str | None,
    ctx,
) -> SurfaceTelegramConfig:
    resolved_name = str(app_name or "").strip()
    if not resolved_name:
        return SurfaceTelegramConfig()
    if platform is not SurfacePlatform.TELEGRAM:
        raise AgentSurfaceValidationError(
            "A Telegram Mini App can only be set on a Telegram surface"
        )
    app = await get_ready_pod_app_by_name(
        uow=uow,
        pod_id=pod_id,
        app_name=resolved_name,
        ctx=ctx,
    )
    if app is None:
        raise AgentSurfaceValidationError(
            "The selected Telegram Mini App must belong to this pod and be deployed"
        )
    return SurfaceTelegramConfig(app_name=app.name)


async def resolve_surface_config(
    *,
    uow,
    pod_id: UUID,
    platform: SurfacePlatform,
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
    config = surface_config_from_input(config_input, channel_routes=channel_routes)
    config.telegram = await resolve_telegram_config(
        uow=uow,
        pod_id=pod_id,
        platform=platform,
        app_name=config_input.telegram.app_name,
        ctx=ctx,
    )
    return config


async def merge_surface_config(
    *,
    uow,
    existing: SurfaceConfig,
    pod_id: UUID,
    platform: SurfacePlatform,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> SurfaceConfig:
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
        updates["send_policy"] = config_input.send_policy.to_domain()
    if "telegram" in config_input.model_fields_set:
        updates["telegram"] = await resolve_telegram_config(
            uow=uow,
            pod_id=pod_id,
            platform=platform,
            app_name=config_input.telegram.app_name,
            ctx=ctx,
        )
    return existing.model_copy(update=updates)
