"""Turning a caller's surface settings into the config a surface stores.

The HTTP create and update routes and a pod bundle import all arrive here with
the same input, so it is a service rather than part of the API layer: the
bundle reaches it through this module's contracts and has no business importing
the HTTP layer to do so.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceGroupPolicy,
    SurfaceIdentityPolicy,
    SurfacePlatform,
    SurfaceSendPolicy,
    SurfaceSlackConfig,
    SurfaceTelegramConfig,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.domain.surface_config import (
    ContactAnswer,
    SurfaceContactPolicy,
    SurfaceWhatsAppConfig,
)
from app.modules.agent_surfaces.domain.surface_representation import (
    SurfaceBehaviorConfigInput,
    surface_config_from_input,
)
from app.modules.apps.contracts import get_ready_pod_app_by_name
from app.core.authorization.permissions import Permissions
from app.modules.pod.contracts.members import pod_member_holds


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


async def resolve_contact_policy(
    *,
    uow,
    pod_id: UUID,
    requested: SurfaceContactPolicy,
    current: SurfaceContactPolicy | None,
    ctx,
) -> SurfaceContactPolicy:
    """The contact policy to store: who answers, and who looks after them.

    Turning contacts on with nobody named makes the person turning them on the
    one their conversations belong to -- the way bringing a bot into a group
    makes that member answer for it. Whoever is named must be an editor or
    admin of the pod: a conversation belongs to somebody, questions passed on
    must reach somebody who can act on them, and contacts' chats are not every
    member's to read.
    """
    looked_after_by = requested.looked_after_by or (
        current.looked_after_by if current is not None else None
    )
    if requested.answer is not ContactAnswer.OFF and looked_after_by is None:
        looked_after_by = getattr(ctx, "user_id", None)
    if looked_after_by is not None and not await pod_member_holds(
        uow, pod_id, looked_after_by, Permissions.CONTACT_READ
    ):
        raise AgentSurfaceValidationError(
            "Contacts must be looked after by an editor or admin of this pod"
        )
    return SurfaceContactPolicy(
        answer=requested.answer, looked_after_by=looked_after_by
    )


async def resolve_surface_config(
    *,
    uow,
    pod_id: UUID,
    platform: SurfacePlatform,
    config_input: SurfaceBehaviorConfigInput,
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
    config.contacts = await resolve_contact_policy(
        uow=uow,
        pod_id=pod_id,
        requested=config.contacts,
        current=None,
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
    if "whatsapp" in config_input.model_fields_set:
        updates["whatsapp"] = SurfaceWhatsAppConfig(
            reengage_template=config_input.whatsapp.reengage_template,
            language=config_input.whatsapp.language,
        )
    if "groups" in config_input.model_fields_set:
        updates["groups"] = SurfaceGroupPolicy(
            answers_outsiders=config_input.groups.answers_outsiders
        )
    if "contacts" in config_input.model_fields_set:
        updates["contacts"] = await resolve_contact_policy(
            uow=uow,
            pod_id=pod_id,
            requested=SurfaceContactPolicy(
                answer=config_input.contacts.answer,
                looked_after_by=config_input.contacts.looked_after_by,
            ),
            current=existing.contacts,
            ctx=ctx,
        )
    return existing.model_copy(update=updates)
