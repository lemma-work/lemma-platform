from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

from app.core.authorization.delegation import is_pod_default_agent
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
    # The pod's own assistant is asked about pod-scoped, the same as everywhere
    # else. Its row's id is the pod's, so both arms would name the same thing --
    # but grants match on (type, id), and an AGENT-typed check would newly hit
    # the resource-owner shortcut for whoever created the pod.
    if agent_id is None or is_pod_default_agent(agent_id, pod_id=pod_id):
        return
    await ctx.require(
        action,
        ResourceRef(
            resource_type=ResourceType.AGENT,
            resource_id=agent_id,
            pod_id=pod_id,
        ),
    )


async def _may_perform_surface_agent_action(
    *,
    ctx,
    pod_id: UUID,
    agent_id: UUID | None,
    action: str,
) -> bool:
    """``require_surface_agent_action``'s question, asked rather than told.

    Same two arms — a pod-scoped check for the pod's own assistant, an
    agent-scoped one otherwise — so the two cannot disagree about what an
    editor is.
    """
    if agent_id is None or is_pod_default_agent(agent_id, pod_id=pod_id):
        return await ctx.can(action)
    return await ctx.can(
        action,
        ResourceRef(
            resource_type=ResourceType.AGENT,
            resource_id=agent_id,
            pod_id=pod_id,
        ),
    )


async def surface_setup_for_reader(
    *, service, ctx, pod_id: UUID, surface_name: str
) -> dict[str, object]:
    """This surface's setup state, with only what this reader may be shown.

    ``SurfaceSetupActionField.secret`` is a rendering hint, not an access
    control, and the WhatsApp verify token in one of those fields is what
    re-points the org's webhook subscription. The endpoint is readable with
    ``AGENT_READ``, so every pod member who can list agents was handed it;
    refusing them the whole checklist would be wrong, so the one value goes to a
    reader who could change the surface anyway.
    """
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    return await service.get_surface_setup_by_name(
        pod_id=pod_id,
        name=surface_name,
        reveal_secrets=await _may_perform_surface_agent_action(
            ctx=ctx,
            pod_id=pod_id,
            agent_id=surface.agent_id,
            action=Permissions.AGENT_UPDATE,
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


def _resolve_channel_routes(
    *,
    config_input: SurfaceBehaviorConfigInput,
) -> list[SurfaceChannelRoute]:
    """The channels this surface's agent may be spoken to in.

    No agent resolution and no per-route permission check: a channel is an
    allow-list entry now, so there is no second agent to be authorized against.
    Whoever may configure the surface may say where its one agent answers.
    """
    return [
        SurfaceChannelRoute(
            channel_id=route.channel_id,
            channel_name=route.channel_name,
        )
        for route in config_input.channels
    ]


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
    ctx,
) -> SurfaceSlackConfig:
    """Resolve the Slack block.

    Only the featured app is left. This used to carry everyone's per-person DM
    agent choices forward across a save, and a flag saying whether to honour
    them; both went with the shared bot they were written for.
    """
    resolved_name = str(app_name or "").strip()
    if not resolved_name:
        return SurfaceSlackConfig()
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
    return SurfaceSlackConfig(app_name=app.name)


async def resolve_surface_config(
    *,
    uow,
    pod_id: UUID,
    platform: SurfacePlatform,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> SurfaceConfig:
    channel_routes = _resolve_channel_routes(config_input=config_input)
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
        updates["channels"] = _resolve_channel_routes(config_input=config_input)
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
            ctx=ctx,
        )
    return existing.model_copy(update=updates)
