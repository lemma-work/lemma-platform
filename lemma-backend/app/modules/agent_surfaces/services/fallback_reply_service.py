from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceReplyContext,
    SurfaceReplyKind,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
    SurfacePlatformAdapterPort,
)

logger = get_logger(__name__)
_fallback_incident = DependencyIncident("surface_fallback_delivery", logger=logger)


def signup_message() -> str:
    signup_url = settings.auth_frontend_url.rstrip("/")
    return (
        "Please sign up before chatting with this agent. "
        f"You can get started here: {signup_url}"
    )


def surface_setup_message() -> str:
    frontend_url = settings.frontend_url.rstrip("/")
    return (
        "You're signed in, but no agent surface is configured for you yet. "
        "Open Lemma to set up or select a surface: "
        f"{frontend_url}"
    )


def _pod_access_message(pod_id: UUID) -> str:
    base = settings.auth_frontend_url.rstrip("/")
    return (
        "You're signed up, but don't have access to this workspace yet. "
        f"Request access here: {base}/pods/{pod_id}"
    )


def _can_disclose_pod_access_link(surface: AgentSurfaceEntity) -> bool:
    if surface.credential_mode is not SurfaceCredentialMode.SYSTEM:
        return True
    if surface.account_id is not None:
        return True
    return surface.surface_type not in {
        SurfacePlatform.TELEGRAM,
        SurfacePlatform.WHATSAPP,
    }


def _reply_context(
    *,
    platform: str | SurfacePlatform,
    surface: AgentSurfaceEntity | None,
    parsed: ParsedInboundSurfaceEvent,
    agent_display_name: str,
    reply: tuple[str, dict[str, Any]],
    reply_kind: SurfaceReplyKind,
    include_surface_id: bool = True,
) -> SurfaceReplyContext:
    message, reply_metadata = reply
    return SurfaceReplyContext(
        platform=SurfacePlatform(platform),
        surface_id=surface.id if surface and include_surface_id else None,
        surface_account_id=surface.account_id if surface else None,
        surface_config=surface.config if surface else None,
        agent_display_name=agent_display_name,
        reply_kind=reply_kind,
        reply_message=message,
        reply_metadata=reply_metadata,
        event=parsed,
    )


def unresolved_sender_context(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    adapter: SurfacePlatformAdapterPort,
    agent_display_name: str,
) -> SurfaceReplyContext | None:
    if not parsed.is_dm:
        return None
    identity_reply = adapter.unresolved_sender_reply(parsed)
    reply = identity_reply or (signup_message(), {})
    reply_kind: SurfaceReplyKind = (
        "identity_link" if identity_reply is not None else "signup"
    )
    return _reply_context(
        platform=surface.surface_type,
        surface=surface,
        parsed=parsed,
        agent_display_name=agent_display_name,
        reply=reply,
        reply_kind=reply_kind,
    )


def identity_confirmation_context(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    agent_display_name: str,
    confirmation: tuple[str, dict[str, Any]],
) -> SurfaceReplyContext:
    return _reply_context(
        platform=surface.surface_type,
        surface=surface,
        parsed=parsed,
        agent_display_name=agent_display_name,
        reply=confirmation,
        reply_kind="identity_link",
    )


def nonmember_context(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    agent_display_name: str,
) -> SurfaceReplyContext | None:
    if not parsed.is_dm:
        return None
    disclose_pod = _can_disclose_pod_access_link(surface)
    message = (
        _pod_access_message(surface.pod_id) if disclose_pod else surface_setup_message()
    )
    reply_kind: SurfaceReplyKind = "pod_access" if disclose_pod else "surface_setup"
    return _reply_context(
        platform=surface.surface_type,
        surface=surface,
        parsed=parsed,
        agent_display_name=agent_display_name,
        reply=(message, {}),
        reply_kind=reply_kind,
    )


def surface_setup_context(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    agent_display_name: str,
) -> SurfaceReplyContext | None:
    if not parsed.is_dm:
        return None
    return _reply_context(
        platform=surface.surface_type,
        surface=surface,
        parsed=parsed,
        agent_display_name=agent_display_name,
        reply=(surface_setup_message(), {}),
        reply_kind="surface_setup",
    )


async def prepare_unrouted_context(
    *,
    platform: str,
    surface: AgentSurfaceEntity | None,
    parsed: ParsedInboundSurfaceEvent,
    adapter: SurfacePlatformAdapterPort,
    resolved_user: ResolvedSurfaceUser,
    agent_display_name: str,
    event_dedup_store: SurfaceEventDedupStorePort,
) -> SurfaceReplyContext | None:
    claimed = await event_dedup_store.claim_message(
        surface_installation_id=None,
        platform=platform,
        external_channel_id=parsed.external_channel_id,
        external_thread_id=parsed.external_thread_id,
        external_message_id=parsed.external_message_id,
    )
    if not claimed:
        logger.debug(
            "agent_surfaces.fallback_reply_service.agent_surface_ignored_duplicate_unrouted.observed",
            external_channel_id=parsed.external_channel_id,
        )
        return None

    identity_reply = adapter.unresolved_sender_reply(parsed)
    confirmation = adapter.linked_sender_confirmation(parsed)
    reply, reply_kind = _unrouted_reply(
        resolved_user=resolved_user,
        identity_reply=identity_reply,
        confirmation=confirmation,
    )
    logger.debug(
        "agent_surfaces.fallback_reply_service.agent_surface_prepared_unrouted_fallback.observed",
        reply_kind=reply_kind,
    )
    return _reply_context(
        platform=platform,
        surface=surface,
        parsed=parsed,
        agent_display_name=agent_display_name,
        reply=reply,
        reply_kind=reply_kind,
        include_surface_id=False,
    )


def _unrouted_reply(
    *,
    resolved_user: ResolvedSurfaceUser,
    identity_reply: tuple[str, dict[str, Any]] | None,
    confirmation: tuple[str, dict[str, Any]] | None,
) -> tuple[tuple[str, dict[str, Any]], SurfaceReplyKind]:
    if resolved_user.internal_user_id is None:
        return (
            identity_reply or (signup_message(), {}),
            "identity_link" if identity_reply is not None else "signup",
        )
    if confirmation is not None:
        return confirmation, "identity_link"
    return (surface_setup_message(), {}), "surface_setup"


def has_delivery_credentials(
    platform: SurfacePlatform,
    credentials: dict[str, Any],
) -> bool:
    normalized = str(platform).upper()
    if normalized == SurfacePlatform.WHATSAPP:
        return bool(credentials.get("access_token"))
    if normalized == SurfacePlatform.TELEGRAM:
        return bool(credentials.get("bot_token"))
    if normalized == SurfacePlatform.SLACK:
        raw_response = credentials.get("raw_response") or {}
        return bool(
            credentials.get("access_token")
            or credentials.get("bot_token")
            or raw_response.get("access_token")
        )
    if normalized == SurfacePlatform.TEAMS:
        return bool(
            surface_settings.microsoft_bot_app_id
            and surface_settings.microsoft_bot_app_password
        )
    if normalized == SurfacePlatform.RESEND:
        return bool(credentials.get("api_key"))
    return any(value for value in credentials.values())


async def deliver_fallback_reply(
    *,
    adapter: SurfacePlatformAdapterPort,
    context: SurfaceReplyContext,
    credentials: dict[str, Any],
) -> None:
    if not has_delivery_credentials(context.platform, credentials):
        _fallback_incident.record_failure(
            error_type="MissingCredentials",
        )
        return
    try:
        await adapter.send_message(
            credentials=credentials,
            event=context.event,
            message=context.reply_message or signup_message(),
            metadata={
                "agent_display_name": context.agent_display_name,
                **dict(context.reply_metadata or {}),
            },
        )
    except Exception as exc:
        _fallback_incident.record_failure(error_type=type(exc).__name__)
    else:
        _fallback_incident.record_success()
