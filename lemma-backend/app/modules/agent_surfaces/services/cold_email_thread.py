"""Addressing a person by email who has never written to us.

Chat platforms cannot start a conversation — a bot with no prior thread has
nowhere to put a message, which is why ``send_to_member`` simply returns False.
Email can, and that asymmetry is the entire reason this module exists.

The hard part is not the send, it is making the *reply* come back to the right
place. An inbound email is matched to a conversation by
``get_by_external_thread(surface_id, platform, external_channel_id,
external_thread_id, external_user_id)``, and the Resend parser derives the
thread root as ``references[0] or in_reply_to or message_id``. So we plant a
seed id of our own in the outbound ``References`` header and leave
``In-Reply-To`` empty: a reply's ``References`` is the original's plus the
original's ``Message-ID``, which puts our seed first and makes it the thread
root. This is the ordinary ticketing-system dangling-reference pattern, and it
needs no control over the ``Message-ID`` the provider generates — which we do
not have.

The coordinate arithmetic is pure, so what decides whether a reply is ever
seen again can be tested without a mail provider or a database. The two async
functions at the end are the send and the link that records it, shared by a
notification's cold open and a chat reply rerouted to email when the chat's
reply window has closed (``reply_window_fallback``).
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.models import ColdEmailSendResult
from app.modules.agent_surfaces.domain.ports import ColdEmailThread
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text

# ``external_thread_id`` is a String(255). A message id longer than that is
# truncated on write and then never matches the reply, so the budget is real.
MAX_THREAD_ID_LENGTH = 255

_FALLBACK_DOMAIN = "lemma.invalid"


def _domain_of(address: str | None) -> str:
    """The domain half of the surface's own address.

    ``.invalid`` is reserved by RFC 2606 and can never resolve, so a surface
    with no recorded address yields a seed that is still a syntactically valid
    Message-ID but is obviously not a real mailbox.
    """
    _, _, domain = (address or "").partition("@")
    return domain.strip().lower() or _FALLBACK_DOMAIN


def cold_thread_seed_id(*, notification_id: UUID, surface: AgentSurfaceEntity) -> str:
    """The Message-ID we plant so the reply can be recognised.

    Derived from the notification id rather than randomly, so re-delivering the
    same notification lands on the same thread instead of stacking a second
    conversation on somebody who has not even replied to the first.
    """
    seed = f"<lemma-notification-{notification_id}@{_domain_of(surface.surface_identity_email)}>"
    return seed[:MAX_THREAD_ID_LENGTH]


def cold_conversation_seed_id(
    *, conversation_id: UUID, surface: AgentSurfaceEntity
) -> str:
    """The seed for a chat reply rerouted to email, one per conversation.

    Per conversation rather than per message, so every reply that missed the
    chat lands in one email thread -- and the person's reply to any of them
    comes back to the conversation they were having.
    """
    seed = f"<lemma-conversation-{conversation_id}@{_domain_of(surface.surface_identity_email)}>"
    return seed[:MAX_THREAD_ID_LENGTH]


def cold_email_channel_id(surface: AgentSurfaceEntity) -> str | None:
    """Our own mailbox, lowercased — what the parser records as the channel.

    The inbound parser takes this from the ``to`` of the reply, so it must be
    the address we sent *from*, not the recipient.
    """
    address = (surface.surface_identity_email or "").strip().lower()
    return address or None


def build_cold_email_thread(
    *,
    surface: AgentSurfaceEntity,
    recipient_email: str,
    sent: ColdEmailSendResult,
) -> ColdEmailThread:
    """Turn a completed send into the thread coordinates a reply will match."""
    event = ParsedInboundSurfaceEvent(
        platform=surface.surface_type,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id=cold_email_channel_id(surface),
        external_thread_id=sent.external_thread_id,
        external_message_id=sent.external_message_id,
        sender_external_user_id=recipient_email.strip().lower(),
        sender_email=recipient_email.strip().lower(),
        # They have not said anything yet. An empty body is the honest record,
        # and nothing replays this as conversation history.
        message_text="",
        is_dm=True,
        should_start_conversation=True,
        reply_target=dict(sent.reply_target),
    )
    return ColdEmailThread(
        external_thread_id=sent.external_thread_id,
        external_channel_id=cold_email_channel_id(surface),
        external_message_id=sent.external_message_id,
        last_event=event.model_dump(mode="json"),
    )


async def open_cold_email_thread(
    *,
    adapter: SurfacePlatformAdapterPort | None,
    credentials: dict[str, object],
    surface: AgentSurfaceEntity,
    recipient_email: str,
    subject: str,
    message: str,
    thread_seed_id: str,
    metadata: dict[str, object] | None = None,
) -> ColdEmailThread | None:
    """Email somebody who has never written to this mailbox.

    ``None`` when the surface is inactive, has no adapter, the message is empty
    once cleaned, or the platform cannot start a thread -- all of which are
    "no", not failures.
    """
    if not surface.is_active or adapter is None:
        return None
    clean_message = sanitize_user_visible_text(message)
    if not clean_message:
        return None
    sent = await adapter.send_cold_email(
        credentials=credentials,
        recipient_email=recipient_email,
        subject=subject,
        message=clean_message,
        thread_seed_id=thread_seed_id,
        metadata=metadata,
    )
    if sent is None:
        return None
    return build_cold_email_thread(
        surface=surface, recipient_email=recipient_email, sent=sent
    )


async def remember_cold_email_thread(
    links: SurfaceConversationLinkRepository,
    thread: ColdEmailThread,
    *,
    surface: AgentSurfaceEntity,
    recipient_email: str | None,
    conversation_id: UUID,
) -> None:
    """Record the thread so the reply resolves to this conversation.

    Get-then-create rather than a blind insert: the seed is derived from the
    notification or the conversation, so a second send arrives here with a link
    already written and must reuse it instead of tripping the unique index.
    """
    external_user_id = (recipient_email or "").strip().lower() or None
    existing = await links.get_by_external_thread(
        surface_id=surface.id,
        platform=surface.surface_type.value,
        external_channel_id=thread.external_channel_id,
        external_thread_id=thread.external_thread_id,
        external_user_id=external_user_id,
    )
    if existing is not None:
        return
    await links.create(
        AgentSurfaceConversationLink(
            surface_id=surface.id,
            conversation_id=conversation_id,
            platform=surface.surface_type.value,
            external_channel_id=thread.external_channel_id,
            external_thread_id=thread.external_thread_id,
            external_user_id=external_user_id,
            # The agent the conversation was opened under. Inbound compares
            # it to the agent the reply routes to, and a link that names
            # nobody reads as the pod's own assistant -- so for any other
            # agent's mailbox the person's first reply would be cut into a
            # new conversation, away from the message it answers.
            routed_agent_id=surface.agent_id,
            conversation_kind="EMAIL",
            route_key="email",
            last_event=thread.last_event,
            last_message_id=thread.external_message_id,
            # They have not written to us. Claiming otherwise would let an
            # outbound masquerade as inbound activity in channel ranking.
            last_inbound_at=None,
        )
    )


__all__ = [
    "MAX_THREAD_ID_LENGTH",
    "build_cold_email_thread",
    "cold_conversation_seed_id",
    "cold_email_channel_id",
    "cold_thread_seed_id",
    "open_cold_email_thread",
    "remember_cold_email_thread",
]
