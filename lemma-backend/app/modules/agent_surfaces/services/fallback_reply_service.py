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
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
)

logger = get_logger(__name__)
_fallback_incident = DependencyIncident("surface_fallback_delivery", logger=logger)


def sender_can_be_answered(parsed: ParsedInboundSurfaceEvent) -> bool:
    """Whether anyone is actually at the address a fallback would reply to.

    Only email can lie about who sent it, and ``_email_sender_is_believable``
    already refuses to resolve an unvouched ``From:`` to a user. The unresolved
    path then replied to that same unvouched address -- so the check protecting
    identity was manufacturing the message it should have prevented: forge a
    ``From:`` header at an agent's mailbox, and Lemma mails whoever the forger
    named. The stricter the authentication check became, the more of these it
    produced.

    Silence is the honest answer to a sender the receiving mail service would
    not vouch for. Nobody is there to read a reply, and somebody else might be.

    Every chat platform asserts its sender inside a signed payload, so this only
    ever narrows email.
    """
    if not parsed.platform.is_email:
        return True
    return parsed.sender_authentication == EmailAuthenticationVerdict.PASS


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
    base = settings.frontend_url.rstrip("/")
    return (
        "You're signed up, but don't have access to this workspace yet. "
        f"Request access here: {base}/pod/{pod_id}"
    )


# Platforms that can answer one person inside a room without the room reading it.
_ANSWERS_PRIVATELY_IN_A_ROOM = frozenset({SurfacePlatform.SLACK.value})


def private_reply_metadata(parsed: ParsedInboundSurfaceEvent) -> dict[str, str] | None:
    """How to answer this person without answering the room -- None for nowhere.

    Every fallback used to be gated on ``is_dm``, which asked one question in
    place of two and got both backwards. It was most cautious in a channel,
    where caution was unnecessary, and most talkative to an anonymous inbox,
    where it was not.

    A Slack channel is not a public place: everyone in it was admitted by a
    workspace an organisation authorised, so an unrecognised sender there is a
    colleague without a Lemma account -- the safest person the system meets.
    Going quiet in front of them is the one case where silence has no defence,
    and it makes being added to the channel buy nothing.

    What the old gate actually protected was the *room*, not the boundary. So
    the room keeps its protection and the person still gets their answer, which
    is the same trade Slack already makes with an ephemeral when Lemma is added
    to a channel. Where a platform cannot answer privately, silence stands --
    a public notice about somebody's account is worse than none.
    """
    if parsed.is_dm:
        return {}
    if str(parsed.platform).upper() not in _ANSWERS_PRIVATELY_IN_A_ROOM:
        return None
    sender = str(parsed.sender_external_user_id or "").strip()
    return {"ephemeral_to": sender} if sender else None


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
    audience = private_reply_metadata(parsed)
    if audience is None:
        return None
    if not sender_can_be_answered(parsed):
        return None
    identity_reply = adapter.unresolved_sender_reply(parsed)
    message, reply_metadata = identity_reply or (signup_message(), {})
    reply_kind: SurfaceReplyKind = (
        "identity_link" if identity_reply is not None else "signup"
    )
    return _reply_context(
        platform=surface.surface_type,
        surface=surface,
        parsed=parsed,
        agent_display_name=agent_display_name,
        reply=(message, {**reply_metadata, **audience}),
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
    audience = private_reply_metadata(parsed)
    if audience is None:
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
        reply=(message, audience),
        reply_kind=reply_kind,
    )


def surface_setup_context(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    agent_display_name: str,
) -> SurfaceReplyContext | None:
    audience = private_reply_metadata(parsed)
    if audience is None:
        return None
    return _reply_context(
        platform=surface.surface_type,
        surface=surface,
        parsed=parsed,
        agent_display_name=agent_display_name,
        reply=(surface_setup_message(), audience),
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
    if not sender_can_be_answered(parsed):
        return None
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
    event_dedup_store: SurfaceEventDedupStorePort,
) -> None:
    # Every fallback reply on every platform passes here, which makes it the one
    # place the window can be held for all four reply kinds at once.
    if not await event_dedup_store.claim_stranger_reply(
        platform=str(context.platform),
        surface_installation_id=context.surface_id,
        sender_external_user_id=context.event.sender_external_user_id,
    ):
        logger.debug(
            "agent_surfaces.fallback_reply.surface_fallback_within_window.observed",
            platform=str(context.platform),
            reply_kind=context.reply_kind,
        )
        return
    if not has_delivery_credentials(context.platform, credentials):
        # Not a dependency wobbling: the surface has no way to send at all, so
        # every unrecognised sender on it is being ignored rather than told how
        # to get access. `_fallback_incident` reported that as one anonymous
        # `dependency.degraded` after three of them, and the surface is the one
        # fact needed to repair it.
        logger.warning(
            "agent_surfaces.fallback_reply.surface_fallback_no_credentials.degraded",
            platform=str(context.platform),
            surface_id=str(context.surface_id) if context.surface_id else None,
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
