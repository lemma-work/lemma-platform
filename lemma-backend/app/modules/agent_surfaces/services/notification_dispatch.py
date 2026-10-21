"""Putting a line meant for a member on the first channel that takes it.

The line is already in their channel when this runs: what is decided here is
only whether a platform carries it too, and which. The outbound copy is
written into their conversation before the platform is called, so a failed
send still leaves the agent handling their reply a record of what was asked.
What became of it is the caller's to record, on the line's delivery.
"""

from __future__ import annotations

from uuid import UUID

from httpx import HTTPError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.platforms.delivery import SendOutcome, track_sends
from app.modules.agent_surfaces.services.delivery_outcome import RESENDS_SAFELY
from app.modules.agent_surfaces.services.notification_channels import can_cold_open
from app.modules.agent_surfaces.services.notification_delivery import (
    Delivered,
    DeliveryChannel,
    Outbound,
    PlatformSent,
    UndeliverableReason,
)
from app.modules.agent_surfaces.services.notification_egress import NotificationEgress
from app.modules.agent_surfaces.services.notification_rate_limiter import (
    EmailBudget,
    EmailSendLimitExceeded,
)

logger = get_logger(__name__)


async def send_on_first_channel(
    outbound: Outbound,
    *,
    channels: list[DeliveryChannel],
    message: str,
    agent_name: str | None,
    uow: SqlAlchemyUnitOfWork,
    egress: NotificationEgress,
    email_budget: EmailBudget | None,
) -> Delivered:
    """Try each channel in order; the first that delivers wins.

    Three copies of one message across three apps is how a useful feature
    comes to read as spam.
    """
    last_error: str | None = None
    outage = False
    for channel in channels:
        conversation_id: UUID | None = None
        sent: PlatformSent | None = None
        with track_sends() as sends:
            try:
                refusal = await _email_budget_refusal(channel, outbound, email_budget)
                if refusal is not None:
                    last_error = refusal
                    continue
                conversation_id = await egress.conversation_for(
                    channel, outbound=outbound
                )
                if conversation_id is None:
                    last_error = UndeliverableReason.NEVER_INTERACTED
                    continue
                await egress.persist_outbound(
                    conversation_id=conversation_id, outbound=outbound, message=message
                )
                # Committed before the platform call: the copy is readable
                # whatever the platform does, and no pooled connection is held
                # across it.
                await uow.commit()
                sent = await egress.send(
                    channel,
                    conversation_id=conversation_id,
                    outbound=outbound,
                    message=message,
                    agent_name=agent_name,
                )
                if sent is None:
                    last_error = (
                        UndeliverableReason.COLD_OPEN_UNSUPPORTED
                        if channel.source is None
                        else f"{channel.platform.value} send returned no delivery."
                    )
            except (AgentSurfaceError, HTTPError, OSError) as exc:
                # Deliberately not `Exception`: a bug in our own code must
                # surface rather than be reported as "that channel didn't work".
                last_error = f"{channel.platform.value}: {exc}"
                outage = True
                logger.warning(
                    "agent_surfaces.notification_service.channel_send_failed.degraded",
                    notification_id=str(outbound.id),
                    platform=channel.platform.value,
                    error=str(exc),
                )
        if sent is not None and conversation_id is not None:
            return Delivered(
                platform=channel.platform.value,
                conversation_id=conversation_id,
                external_message_id=sent.external_message_id,
            )
        if _may_be_there(sends, channel):
            # The platform may have it, or part of it: another channel now
            # would be a second copy of the push. Its outcome is the delivery's.
            return Delivered(reason=last_error or "It may not have arrived.")
    return Delivered(
        reason=last_error or "No channel accepted the message.", retryable=outage
    )


def _may_be_there(sends: SendOutcome, channel: DeliveryChannel) -> bool:
    """Whether a send on ``channel`` that took nothing may still have
    arrived: part of it went, or the platform never answered -- unless it is
    one that dedupes a resend by its key, where trying again is harmless."""
    if sends.partial:
        return True
    return sends.ambiguous and channel.platform.value not in RESENDS_SAFELY


async def _email_budget_refusal(
    channel: DeliveryChannel, outbound: Outbound, email_budget: EmailBudget | None
) -> str | None:
    """Why this channel may not carry it, when the pod's email budget is spent.

    Charged per email: a message that went out on Slack costs the shared
    sending domain nothing. Refused rather than raised -- the line is in their
    channel either way.
    """
    if not can_cold_open(channel.surface) or email_budget is None:
        return None
    try:
        await email_budget.check(pod_id=outbound.pod_id)
    except EmailSendLimitExceeded as exc:
        return str(exc)
    return None
