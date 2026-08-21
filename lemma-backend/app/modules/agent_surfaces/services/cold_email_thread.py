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

Everything here is pure, so the coordinate arithmetic that decides whether a
reply is ever seen again can be tested without a mail provider or a database.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.models import ColdEmailSendResult
from app.modules.agent_surfaces.domain.ports import ColdEmailThread

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


__all__ = [
    "MAX_THREAD_ID_LENGTH",
    "build_cold_email_thread",
    "cold_email_channel_id",
    "cold_thread_seed_id",
]
