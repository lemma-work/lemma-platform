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
recent conversation, so the agent and the contact both see it in context. At
most ``surface_contact_follow_ups_per_contact_per_day`` a day per contact.

Sent first, then written into the conversation, and written as not sent when
the platform refused it: the thread never shows the contact as having been told
something they were not.
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
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.services.contact_windows import (
    ContactWindows,
    Window,
)
from app.modules.contacts.contracts import (
    ContactHandle,
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


def _unsubscribe_line(handle: ContactHandle) -> str:
    base = str(settings.api_url).rstrip("/")
    link = f"{base}/public/contacts/unsubscribe?token={quote(unsubscribe_token(handle.id))}"
    return f"\n\n--\nTo stop these emails: {link}"


def _check_channel(
    platform: str, handles: list[ContactHandle], now: datetime
) -> ContactHandle | None:
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
    windows: ContactWindows | None = None,
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
    await _within_the_days_follow_ups(windows or ContactWindows(), contact_id)
    if handle is not None and handle.kind is IdentityKind.EMAIL:
        text += _unsubscribe_line(handle)
    # A web chat has nobody to hand it to: it waits in the conversation for
    # the contact's next visit, which is what writing it down does.
    delivered = (
        None
        if platform == WEB_PLATFORM
        else await _send(uow_factory, conversation_id, text, platform)
    )
    async with uow_factory() as uow:
        await append_follow_up(
            uow,
            conversation_id=conversation_id,
            message=text,
            sent_by_user_id=sent_by_user_id,
            delivered=delivered,
        )
    if delivered is False:
        raise FollowUpRefused(
            "The message could not be delivered. Try again later.",
            code="not_delivered",
            status_code=502,
        )
    return FollowUpSent(conversation_id, platform, delivered=bool(delivered))


async def _within_the_days_follow_ups(
    windows: ContactWindows, contact_id: UUID
) -> None:
    window = await windows.follow_up(contact_id)
    if window is Window.SHUT:
        raise FollowUpRefused(
            "That is as many messages as one contact gets in a day",
            code="rate_limited",
            status_code=429,
        )
    if window is Window.UNKNOWN:
        raise FollowUpRefused(
            "Messages to contacts are unavailable right now. Try again shortly.",
            code="unavailable",
            status_code=503,
        )


async def _send(
    uow_factory: UnitOfWorkFactory, conversation_id: UUID, text: str, platform: str
) -> bool:
    """Hand the follow-up to the platform; whether it took it."""
    try:
        async with uow_factory() as uow:
            delivered = await build_surface_egress(
                uow
            ).send_agent_message_for_conversation(
                conversation_id=conversation_id, message=text
            )
    except PLATFORM_TRANSPORT_ERRORS as exc:
        logger.warning(
            "agent_surfaces.contact_follow_ups.send_failed.degraded",
            platform=platform,
            error_type=type(exc).__name__,
        )
        delivered = False
    logger.info(
        "agent_surfaces.contact_follow_ups.sent.observed",
        platform=platform,
        delivered=delivered,
    )
    return delivered
