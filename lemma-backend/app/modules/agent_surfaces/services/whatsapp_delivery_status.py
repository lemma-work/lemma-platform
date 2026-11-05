"""Correcting a WhatsApp send that Meta reported it never delivered.

Cloud API answers a send with 200 and reports most failures afterwards, in a
``statuses`` webhook naming the message id -- a recipient outside the 24-hour
window (131047), a number that cannot receive (131026). Every send of a line
records that id on its delivery row as soon as Meta answers -- before the
outcome is recorded -- so the report finds the row by it, however early it
comes, and marks that one delivery ``DEAD`` with the reason a member reads.
An id that is not on any row (a progress post, an onboarding step) is only
logged.

Applied beside ``group_updates`` on every webhook, before the message paths and
never instead of them.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.ingress_request import SurfaceIngressRequest
from app.modules.channels.contracts.deliveries import (
    delivery_by_external,
    delivery_undelivered,
)
from app.modules.agent_surfaces.platforms.whatsapp.parser import (
    WhatsAppFailedDelivery,
    failed_deliveries,
)

logger = get_logger(__name__)

#: Meta's codes for the two failures a person can do something about.
_OUTSIDE_REPLY_WINDOW = "131047"
_CANNOT_RECEIVE = "131026"


def failure_reason(failure: WhatsAppFailedDelivery) -> str:
    """What the notification's sender is told, in place of "delivered"."""
    if failure.code == _OUTSIDE_REPLY_WINDOW:
        return (
            "WhatsApp did not deliver it: more than 24 hours have passed since "
            "they last wrote, and only pre-approved template messages are allowed "
            "after that. Ask them to message the bot, or reach them another way."
        )
    if failure.code == _CANNOT_RECEIVE:
        return (
            "WhatsApp did not deliver it: this number cannot receive messages "
            "from the business right now."
        )
    detail = f" ({failure.reason})" if failure.reason else ""
    return f"WhatsApp reported the message as not delivered{detail}."


async def apply_delivery_statuses(
    uow: SqlAlchemyUnitOfWork, request: SurfaceIngressRequest
) -> None:
    """Mark the deliveries a ``statuses`` webhook says never arrived."""
    if request.payload.get("object") != "whatsapp_business_account":
        return
    for failure in failed_deliveries(request.payload):
        delivery = await delivery_by_external(
            uow, platform="WHATSAPP", external_message_id=failure.message_id
        )
        marked = delivery is not None and await delivery_undelivered(
            uow, delivery.id, reason=failure_reason(failure)
        )
        logger.warning(
            "agent_surfaces.whatsapp.delivery_failed.degraded",
            error_code=failure.code,
            reason=failure.reason,
            deliveries_marked=int(marked),
        )
