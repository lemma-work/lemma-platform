"""A reply that the chat will no longer take, sent by email instead.

WhatsApp lets a business send free-form messages for 24 hours after the
person's last message, and nothing after that without a pre-approved template.
Meta accepts the call and reports the failure later, asynchronously -- so a run
that finished on the 25th hour, a workflow's ``surface.send``, or a scheduled
follow-up all "succeeded" and reached nobody.

The notification path already knew this (``reply_window_open`` keeps closed
chats out of its candidates). Every other path delivers through
``SurfaceDelivery.deliver_envelope``, and this is the check it makes first: if
the window has closed, the reply goes to the conversation's own user by email,
in an email thread linked to the same conversation so their reply comes back to
it. Somebody who is not that user -- a stranger in a shared chat -- has no
address we may use, and their reply is reported undelivered instead.

Templates are deliberately not used: they need per-business approval in Meta's
console, and an email that always works beats a template that may not exist.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.delivery_result import SurfaceDeliveryResult
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.domain.groups import OUTSIDERS_LINK_USER
from app.modules.agent_surfaces.domain.ports import ColdEmailThread
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.services.cold_email_thread import (
    cold_conversation_seed_id,
    open_cold_email_thread,
    remember_cold_email_thread,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.notification_channels import (
    NotificationChannelResolver,
)
from app.modules.agent_surfaces.services.free_text_answer import (
    remember_answer_will_be_typed,
    tool_call_id_of,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    EMAIL_CHANNEL,
    UndeliverableReason,
    channel_label,
    reply_window_open,
)
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget

logger = get_logger(__name__)


def reply_window_closed(target: SurfaceEgressTarget) -> bool:
    """Has this direct chat's free-form reply window closed?

    Direct chats only. Whether Meta applies the window to a group the bot
    created is not something we have confirmed, and refusing a group reply on a
    guess would silence a group that works.
    """
    if not target.event.is_dm or target.link.conversation_kind == "CHANNEL":
        return False
    return not reply_window_open(
        platform=target.surface.surface_type,
        last_inbound_at=target.link.inbound_activity_at,
    )


def window_closed_reason(target: SurfaceEgressTarget) -> str:
    return UndeliverableReason.window_closed_on(target.surface.surface_type.value)


@dataclass(frozen=True, slots=True)
class EmailFallbackPorts:
    """What the fallback reads and sends with, all built from one unit of work."""

    identities: ExternalSurfaceUserRepository
    channels: NotificationChannelResolver
    credentials: SurfaceCredentialResolver
    links: SurfaceConversationLinkRepository
    adapters: SurfacePlatformAdapterRegistry
    open_thread: Callable[..., Awaitable[ColdEmailThread | None]] = (
        open_cold_email_thread
    )
    remember_thread: Callable[..., Awaitable[None]] = remember_cold_email_thread
    remember_typed: Callable[..., Awaitable[None]] = remember_answer_will_be_typed

    @classmethod
    def for_uow(cls, uow: SqlAlchemyUnitOfWork) -> EmailFallbackPorts:
        from app.modules.agent_surfaces.composition import (
            build_notification_channel_resolver,
        )

        return cls(
            identities=ExternalSurfaceUserRepository(uow),
            channels=build_notification_channel_resolver(uow),
            credentials=SurfaceCredentialResolver(uow=uow),
            links=SurfaceConversationLinkRepository(uow),
            adapters=SurfacePlatformAdapterRegistry(),
        )


async def deliver_after_the_window(
    uow: SqlAlchemyUnitOfWork,
    target: SurfaceEgressTarget,
    *,
    envelope: SurfaceEnvelope,
    metadata: dict[str, object],
    conversation_id: UUID,
    ports: EmailFallbackPorts | None = None,
) -> SurfaceDeliveryResult:
    """Email the conversation's own user what the closed chat would have shown.

    Undelivered -- with the reason a person can act on -- when the chat is
    someone else's, they have no address, or no mailbox can send.
    """
    ports = ports or EmailFallbackPorts.for_uow(uow)
    closed = window_closed_reason(target)
    recipient = await _own_user_in_this_chat(ports.identities, target)
    if recipient is None:
        logger.info(
            "agent_surfaces.reply_window_fallback.outsider_window_closed.observed",
            conversation_id=str(conversation_id),
            platform=target.surface.surface_type.value,
        )
        return SurfaceDeliveryResult.undelivered(
            UndeliverableReason.REPLY_WINDOW_CLOSED
        )
    channels, refusal = await ports.channels.resolve(
        pod_id=target.pod_id,
        recipient_user_id=recipient,
        actor_agent_id=target.link.routed_agent_id or target.surface.agent_id,
        agent_name=str(metadata.get("agent_display_name") or "") or None,
        channel=EMAIL_CHANNEL,
    )
    channel = next((found for found in channels if found.email_address), None)
    if channel is None or channel.email_address is None:
        return SurfaceDeliveryResult.undelivered(f"{closed} {refusal}".strip())
    mailbox = channel.surface
    credentials = await ports.credentials.for_surface(mailbox)
    async with connection_released(uow.session):
        thread = await ports.open_thread(
            adapter=ports.adapters.get(mailbox.surface_type),
            credentials=credentials,
            surface=mailbox,
            recipient_email=channel.email_address,
            subject=_subject(target, metadata),
            message=_email_body(target, envelope),
            thread_seed_id=cold_conversation_seed_id(
                conversation_id=conversation_id, surface=mailbox
            ),
            metadata=dict(metadata),
        )
    if thread is None:
        return SurfaceDeliveryResult.undelivered(
            f"{closed} {UndeliverableReason.COLD_OPEN_UNSUPPORTED}"
        )
    await ports.remember_thread(
        ports.links,
        thread,
        surface=mailbox,
        recipient_email=channel.email_address,
        conversation_id=conversation_id,
    )
    prompt = envelope.choices or envelope.decision
    if prompt is not None:
        # Words are the only way to answer an email, so the reply is the answer.
        await ports.remember_typed(
            uow, conversation_id=conversation_id, tool_call_id=tool_call_id_of(prompt)
        )
    logger.info(
        "agent_surfaces.reply_window_fallback.sent_by_email.observed",
        conversation_id=str(conversation_id),
        platform=target.surface.surface_type.value,
    )
    return SurfaceDeliveryResult.by_email_because(closed)


async def _own_user_in_this_chat(
    identities: ExternalSurfaceUserRepository, target: SurfaceEgressTarget
) -> UUID | None:
    """The conversation's user, if they are the person on the other end.

    Their own email is ours to use; anybody else's is not, and a stranger's
    chat must never put the owner's inbox in front of them -- or the stranger's
    words in the owner's inbox as though addressed to them.
    """
    owner = target.conversation_user_id
    sender = target.link.external_user_id
    if owner is None or not sender or sender == OUTSIDERS_LINK_USER:
        return None
    identity = await identities.get_by_identity(
        platform=target.surface.surface_type.value,
        tenant_id=None,
        external_user_id=sender,
    )
    if identity is None or identity.resolved_user_id != owner:
        return None
    return owner


def _subject(target: SurfaceEgressTarget, metadata: dict[str, object]) -> str:
    speaker = str(metadata.get("agent_display_name") or "Lemma")
    return f"{speaker}: a reply you missed on {_platform_name(target)}"


def _platform_name(target: SurfaceEgressTarget) -> str:
    return channel_label(target.surface.surface_type.value)


def _email_body(target: SurfaceEgressTarget, envelope: SurfaceEnvelope) -> str:
    """The envelope as words, under a line saying why it came by email."""
    platform = _platform_name(target)
    parts = [(envelope.text or "").strip()]
    parts.extend(plan.to_plain_text() for plan in envelope.resources)
    parts.extend(f"Attached in Lemma: {item.file_name}" for item in envelope.files)
    if envelope.voice is not None:
        parts.append(
            envelope.voice.caption or f"A voice note: {envelope.voice.file_name}"
        )
    for prompt in (envelope.choices, envelope.decision):
        if prompt is not None:
            parts.append(prompt.to_plain_text())
    content = "\n\n".join(part for part in parts if part)
    return (
        f"I couldn't send this on {platform}: it only lets me message you within "
        f"a day of your last message there.\n\n{content}\n\n"
        f"Reply to this email, or message me on {platform}, to carry on."
    )


__all__ = [
    "deliver_after_the_window",
    "reply_window_closed",
    "window_closed_reason",
]
