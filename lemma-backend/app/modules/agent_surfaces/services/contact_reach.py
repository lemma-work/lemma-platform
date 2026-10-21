"""The platform facts a contact's outreach rules need, and only those.

``contacts`` decides what may be sent (``contacts.contracts.outreach``); what
it cannot know is a platform's: which kind of handle a place reaches, and
whether the platform lets a business write there now.

**WhatsApp's 24 hours** run from the contact's last message *to that business
number*: Meta keeps the window per number and customer, and past it takes
only pre-approved templates, which are not built here. So the fact is the
place's ``last_inbound_at`` (``channel_sources``, one row per account and
contact handle), not the handle's, which any of the pod's numbers stamps.
Where no place exists yet -- a member writing first -- the handle's is the best
fact there is. Telegram has no window (a bot only ever writes to somebody who
wrote first), email none, a web chat none (it waits for their next visit).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.contacts.contracts import IdentityKind
from app.modules.contacts.contracts.outreach import Address, SendWindow

WHATSAPP_WINDOW = timedelta(hours=24)
WHATSAPP_WINDOW_EXPLANATION = (
    "WhatsApp lets a business write freely only within 24 hours of the "
    "contact's last message, and approved templates are not available"
)

_KIND_FOR_PLATFORM = {
    SurfacePlatform.WHATSAPP.value: IdentityKind.PHONE,
    SurfacePlatform.TELEGRAM.value: IdentityKind.TELEGRAM,
    SurfacePlatform.RESEND.value: IdentityKind.EMAIL,
}


def reach_for(
    platform: str, *, address: str | None, last_inbound_at: datetime | None
) -> tuple[Address, SendWindow | None] | None:
    """Where a line to a contact on ``platform`` goes, and the platform's
    window there; ``None`` for a platform no contact is reached on by handle
    (a web chat, a pod's own workspace)."""
    kind = _KIND_FOR_PLATFORM.get(platform)
    if kind is None:
        return None
    window = None
    if platform == SurfacePlatform.WHATSAPP.value:
        window = SendWindow(
            closes_at=_as_utc(last_inbound_at) + WHATSAPP_WINDOW
            if last_inbound_at is not None
            else None,
            explanation=WHATSAPP_WINDOW_EXPLANATION,
        )
    return Address(kind=kind, value=address or None), window


def _as_utc(when: datetime) -> datetime:
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)
