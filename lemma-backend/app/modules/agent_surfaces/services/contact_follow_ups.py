"""A member writing first to a contact: "your order shipped".

Only where the contact said they want to hear from the pod, and only the way
each channel allows a business to write:

* **WhatsApp:** within 24 hours of the contact's last message. Past it, Meta
  accepts only pre-approved templates, which are not built here.
* **Telegram:** any time -- a bot can only ever write to somebody who wrote to
  it first.
* **Email:** any time, with a line saying how to stop, and a link that does.
* **A web widget:** the message waits in their chat for their next visit.

Never to a handle the contact unsubscribed, and always in the contact's most
recent conversation, so the agent and the contact both see it in context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import UUID

from app.core.config import settings
from app.core.crypto.factory import get_secret_signer
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import append_follow_up
from app.modules.agent_surfaces.composition import build_surface_egress
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.repositories.outside_links import (
    WEB_PLATFORM,
    latest_contact_thread,
)
from app.modules.contacts.contracts import (
    ContactHandleRef,
    IdentityKind,
    contact_handles,
)

logger = get_logger(__name__)

WHATSAPP_WINDOW = timedelta(hours=24)
MAX_FOLLOW_UP_CHARS = 4000

#: The signing purpose of an unsubscribe link: a link for one handle, forever.
UNSUBSCRIBE_PURPOSE = "contact-unsubscribe"

_KIND_FOR_PLATFORM = {
    SurfacePlatform.WHATSAPP.value: IdentityKind.PHONE,
    SurfacePlatform.TELEGRAM.value: IdentityKind.TELEGRAM,
    SurfacePlatform.RESEND.value: IdentityKind.EMAIL,
}


class FollowUpRefused(DomainError):
    def __init__(self, message: str, *, code: str, status_code: int = 409) -> None:
        super().__init__(message, code=code, status_code=status_code)


@dataclass(frozen=True, slots=True)
class FollowUpSent:
    conversation_id: UUID
    platform: str
    delivered: bool


def unsubscribe_token(handle_id: UUID) -> str:
    """A link token that unsubscribes exactly one handle, and can't be forged."""
    payload = str(handle_id)
    return (
        f"{payload}.{get_secret_signer().sign(UNSUBSCRIBE_PURPOSE, payload.encode())}"
    )


def handle_for_unsubscribe(token: str) -> UUID | None:
    payload, _, signature = token.partition(".")
    if not signature or not get_secret_signer().verify(
        UNSUBSCRIBE_PURPOSE, payload.encode(), signature
    ):
        return None
    try:
        return UUID(payload)
    except ValueError:
        return None


def _unsubscribe_line(handle: ContactHandleRef) -> str:
    base = str(settings.api_url).rstrip("/")
    link = f"{base}/public/contacts/unsubscribe?token={quote(unsubscribe_token(handle.id))}"
    return f"\n\n--\nTo stop these emails: {link}"


def _check_channel(
    platform: str, handles: list[ContactHandleRef], now: datetime
) -> ContactHandleRef | None:
    """The handle a follow-up goes to on this platform, or a refusal."""
    kind = _KIND_FOR_PLATFORM.get(platform)
    if kind is None:
        return None
    mine = [handle for handle in handles if handle.kind is kind]
    if not mine:
        raise FollowUpRefused(
            "The contact has no handle on that channel", code="no_handle"
        )
    if any(handle.unsubscribed_at is not None for handle in mine):
        raise FollowUpRefused(
            "The contact asked not to be written to there", code="unsubscribed"
        )
    latest = max(
        mine,
        key=lambda handle: (
            handle.last_inbound_at or datetime.min.replace(tzinfo=timezone.utc)
        ),
    )
    if platform == SurfacePlatform.WHATSAPP.value and (
        latest.last_inbound_at is None or now - latest.last_inbound_at > WHATSAPP_WINDOW
    ):
        raise FollowUpRefused(
            "WhatsApp lets a business write freely only within 24 hours of the "
            "contact's last message, and approved templates are not available",
            code="outside_window",
        )
    return latest


async def send_follow_up(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    contact_id: UUID,
    message: str,
    sent_by_user_id: UUID,
) -> FollowUpSent:
    text = message.strip()[:MAX_FOLLOW_UP_CHARS]
    if not text:
        raise FollowUpRefused("Write something first", code="empty", status_code=400)
    async with uow_factory() as uow:
        handles = await contact_handles(uow, pod_id=pod_id, contact_id=contact_id)
        if not handles:
            raise FollowUpRefused(
                "Contact not found", code="not_found", status_code=404
            )
        thread = await latest_contact_thread(uow.session, contact_id)
        if thread is None:
            raise FollowUpRefused(
                "The contact has not written to the pod yet", code="no_conversation"
            )
        conversation_id, platform = thread
        handle = _check_channel(platform, handles, datetime.now(timezone.utc))
        if handle is not None and handle.kind is IdentityKind.EMAIL:
            text += _unsubscribe_line(handle)
        await append_follow_up(
            uow,
            conversation_id=conversation_id,
            message=text,
            sent_by_user_id=sent_by_user_id,
        )
        await uow.commit()
    if platform == WEB_PLATFORM:
        # Waits in the chat; the widget shows it on their next visit.
        return FollowUpSent(conversation_id, platform, delivered=False)
    async with uow_factory() as uow:
        delivered = await build_surface_egress(uow).send_agent_message_for_conversation(
            conversation_id=conversation_id, message=text
        )
    logger.info(
        "agent_surfaces.contact_follow_ups.sent.observed",
        platform=platform,
        delivered=delivered,
    )
    return FollowUpSent(conversation_id, platform, delivered=delivered)
