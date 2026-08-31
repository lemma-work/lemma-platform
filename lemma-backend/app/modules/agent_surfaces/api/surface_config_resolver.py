from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

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
    SurfaceSlackConfig,
    SurfaceTelegramConfig,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.apps.contracts import get_ready_pod_app_by_name
from app.modules.connectors.contracts import AccountNotFoundError


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


async def require_own_account(
    account_id: UUID | None,
    *,
    user_id: UUID,
    organization_id: UUID | None,
    connector_service,
) -> None:
    """A caller may only point a surface at an account they own.

    Accounts are personal, so binding someone else's is not a permission an
    editor has — it would hand the pod a credential its owner never offered.
    Rebinding to *your own* account is the supported repair when the account a
    surface runs on expires or its owner leaves, and that path passes this check.
    """
    if account_id is None:
        return
    try:
        await connector_service.get_account(account_id, user_id, organization_id)
    # `get_account` answers "not yours" and "no such account" with the same
    # AccountNotFoundError, which is the whole point: the caller learns nothing
    # about accounts they do not own. Caught by its own name rather than through
    # a base class, because which 404 base it carries is exactly what this
    # branch changes.
    except AccountNotFoundError as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                "That account belongs to someone else. Connect your own account "
                "for this platform, then bind the surface to it."
            ),
        ) from exc


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
                # Carried, not derived. "The pod assistant answers here" and
                # "nobody has said" both leave agent_name empty, and dropping
                # the flag turned the first into the second on every save.
                use_pod_assistant=route.use_pod_assistant,
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


async def resolve_slack_config(
    *,
    uow,
    pod_id: UUID,
    platform: SurfacePlatform,
    app_name: str | None,
    dedicated_to_agent: bool = False,
    existing: SurfaceSlackConfig | None = None,
    ctx,
) -> SurfaceSlackConfig:
    """Resolve the Slack block, keeping everyone's DM choices.

    ``dm_agent_by_user`` is carried from ``existing`` rather than taken from
    the request: it is written from inside Slack, one person at a time, and a
    settings save from the web UI has no business replacing it. That holds even
    while ``dedicated_to_agent`` is on and nothing reads the map — turning a
    dedicated bot back into a shared one has to give people their choices back,
    and a save in between must not be what quietly drops them.
    """
    chosen = dict(existing.dm_agent_by_user) if existing else {}
    resolved_name = str(app_name or "").strip()
    if not resolved_name:
        return SurfaceSlackConfig(
            dm_agent_by_user=chosen, dedicated_to_agent=dedicated_to_agent
        )
    if platform is not SurfacePlatform.SLACK:
        raise AgentSurfaceValidationError(
            "A Slack app can only be featured on a Slack surface"
        )
    app = await get_ready_pod_app_by_name(
        uow=uow,
        pod_id=pod_id,
        app_name=resolved_name,
        ctx=ctx,
    )
    if app is None:
        raise AgentSurfaceValidationError(
            "The selected app must belong to this pod and be deployed"
        )
    return SurfaceSlackConfig(
        app_name=app.name,
        dm_agent_by_user=chosen,
        dedicated_to_agent=dedicated_to_agent,
    )


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
    config.slack = await resolve_slack_config(
        uow=uow,
        pod_id=pod_id,
        platform=platform,
        app_name=config_input.slack.app_name,
        dedicated_to_agent=config_input.slack.dedicated_to_agent,
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
        updates["send_policy"] = SurfaceSendPolicy(
            allow_send=config_input.send_policy.allow_send
        )
    if "telegram" in config_input.model_fields_set:
        updates["telegram"] = await resolve_telegram_config(
            uow=uow,
            pod_id=pod_id,
            platform=platform,
            app_name=config_input.telegram.app_name,
            ctx=ctx,
        )
    if "slack" in config_input.model_fields_set:
        updates["slack"] = await resolve_slack_config(
            uow=uow,
            pod_id=pod_id,
            platform=platform,
            app_name=config_input.slack.app_name,
            dedicated_to_agent=config_input.slack.dedicated_to_agent,
            existing=existing.slack,
            ctx=ctx,
        )
    return existing.model_copy(update=updates)
