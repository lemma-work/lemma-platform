"""What Resend reports about an email after it took it: bounced, complained of,
failed.

Resend's webhook carries delivery events beside ``email.received``, each
naming the email by Resend's id -- the id a channel delivery recorded when it
was sent. So the delivery is found by it and given up (``DEAD``, and why), and
for a place's delivery -- a line to a contact -- the address is suppressed in
that pod -- the place's own address, not every address the email was copied
to: a permanent bounce and a spam complaint each mean nobody there writes to
it again until the person writes back (``contacts.outreach``).

A temporary bounce changes nothing: the mail may yet arrive. Nor does one
Resend could not classify (``Undetermined``). An email that is
not one of a channel's deliveries -- an auth email, say -- is somebody else's
to hear about (``identity``'s bounce endpoint).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.channels.contracts import Delivery, source_by_id
from app.modules.channels.contracts.deliveries import (
    delivery_by_external,
    delivery_undelivered,
)
from app.modules.contacts.contracts import IdentityKind
from app.modules.contacts.contracts.outreach import SuppressionReason, opt_out

logger = get_logger(__name__)

_BOUNCED = "email.bounced"
_COMPLAINED = "email.complained"
_FAILED = "email.failed"

#: The Resend events this reads; every other one (received, delivered,
#: opened, ...) is not about a send that failed.
DELIVERY_EVENTS = frozenset({_BOUNCED, _COMPLAINED, _FAILED})


def is_delivery_event(payload: Mapping[str, Any]) -> bool:
    return payload.get("type") in DELIVERY_EVENTS


def _data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, Mapping) else {}


#: Bounce types that do not say the address is gone: the mail may yet
#: arrive, or the provider could not tell (``Undetermined``).
_NOT_PERMANENT = frozenset({"temporary", "transient", "undetermined"})


def _permanent(data: Mapping[str, Any]) -> bool:
    bounce = data.get("bounce")
    kind = str(bounce.get("type") or "") if isinstance(bounce, Mapping) else ""
    return kind.lower() not in _NOT_PERMANENT


def _reason(kind: str, data: Mapping[str, Any]) -> str:
    if kind == _COMPLAINED:
        return "They marked the email as spam; nobody here will email them again."
    if kind == _BOUNCED:
        bounce = data.get("bounce")
        detail = (
            str(bounce.get("message") or "").strip()
            if isinstance(bounce, Mapping)
            else ""
        )
        return "The address does not take mail" + (
            f": {detail[:200]}" if detail else "."
        )
    return "The email could not be sent."


async def _the_address(uow: SqlAlchemyUnitOfWork, delivery: Delivery) -> str | None:
    """The one address the failed delivery was for: its place's person. Not
    every address the email named -- a reply-all copies others, and one
    bounce says nothing about them. ``None`` for a push to a member."""
    if delivery.source_id is None:
        return None
    source = await source_by_id(uow, delivery.source_id)
    if source is None or not source.external_user_id:
        return None
    return source.external_user_id.strip().lower()


async def apply_email_event(
    uow: SqlAlchemyUnitOfWork, payload: Mapping[str, Any]
) -> bool:
    """Record what one Resend delivery event says; whether it named one of the
    channels' deliveries."""
    kind = str(payload.get("type") or "")
    data = _data(payload)
    email_id = str(data.get("email_id") or "").strip()
    if kind not in DELIVERY_EVENTS or not email_id:
        return False
    if kind == _BOUNCED and not _permanent(data):
        return False
    platform = SurfacePlatform.RESEND.value
    delivery = await delivery_by_external(
        uow, platform=platform, external_message_id=email_id
    )
    if delivery is None:
        return False
    await delivery_undelivered(uow, delivery.id, reason=_reason(kind, data))
    suppressed = 0
    address = await _the_address(uow, delivery)
    if address is not None and kind in {_BOUNCED, _COMPLAINED}:
        reason = (
            SuppressionReason.COMPLAINT
            if kind == _COMPLAINED
            else SuppressionReason.BOUNCE
        )
        suppressed = await opt_out(
            uow,
            pod_id=delivery.pod_id,
            kind=IdentityKind.EMAIL,
            value=address,
            reason=reason,
        )
    logger.warning(
        "agent_surfaces.resend.delivery_failed.degraded",
        event_type=kind,
        delivery_id=str(delivery.id),
        suppressed=suppressed,
    )
    return True
