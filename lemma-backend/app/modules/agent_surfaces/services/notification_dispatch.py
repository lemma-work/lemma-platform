"""Putting one notification on the first channel that takes it.

Split out of :class:`NotificationService` by use case. The service owns what a
notification means -- who may receive it, how it is answered, when it expires;
this owns the one moment it leaves Lemma, which is the half with every platform
failure in it and the half whose order of operations is the design: the row and
the outbound message are durable before the platform is called.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from httpx import HTTPError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.notification import NotificationEntity
from app.modules.agent_surfaces.services.notification_channels import can_cold_open
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    UndeliverableReason,
)
from app.modules.agent_surfaces.services.notification_egress import NotificationEgress
from app.modules.agent_surfaces.services.notification_rate_limiter import (
    EmailSendLimitExceeded,
    NotificationRateLimiter,
)

logger = get_logger(__name__)


class NotificationStore(Protocol):
    async def update(self, entity: NotificationEntity) -> NotificationEntity: ...


async def send_on_first_channel(
    notification: NotificationEntity,
    *,
    channels: list[DeliveryChannel],
    message: str,
    agent_name: str | None,
    uow: SqlAlchemyUnitOfWork,
    notifications: NotificationStore,
    egress: NotificationEgress,
    rate_limiter: NotificationRateLimiter | None,
) -> NotificationEntity:
    """Try each channel in order; the first that delivers wins.

    First success wins. Three copies of one message across three apps is how a
    useful feature comes to read as spam.
    """
    last_error: str | None = None
    for channel in channels:
        conversation_id: UUID | None = None
        sent = False
        try:
            refusal = await _email_budget_refusal(channel, notification, rate_limiter)
            if refusal is not None:
                last_error = refusal
                continue

            conversation_id = await egress.conversation_for(
                channel, notification=notification
            )
            if conversation_id is None:
                last_error = UndeliverableReason.NEVER_INTERACTED
                continue

            # Persist before send: a failed platform call must still leave the
            # person a message they can read, and the agent handling their reply
            # a record of what was asked.
            await egress.persist_outbound(
                conversation_id=conversation_id,
                notification=notification,
                message=message,
            )
            # And say the send is under way, in the same commit. A send that
            # landed but whose outcome was never written (the update below
            # failed, the worker died) leaves the row SENDING, which a retry
            # with the same key reads as "may already be on their phone"
            # rather than as never sent.
            notification.mark_sending(
                surface_id=channel.surface.id, platform=channel.platform.value
            )
            notification = await notifications.update(notification)
            # Commit before the platform call, for two reasons that point the
            # same way. It makes "persist before send" mean it — an uncommitted
            # row is not a message anyone can read. And it hands the pooled
            # connection back, so the send below does not hold one (nor the row
            # locks it is sitting on) for the seconds a platform API can take.
            # The update after the send opens its own transaction.
            await uow.commit()
            sent = await egress.send(
                channel,
                conversation_id=conversation_id,
                notification=notification,
                message=message,
                # Unconditionally — wider than the body header. On email it
                # becomes the From display name, which is all an unopened inbox
                # list shows; on chat it is the bot's username and avatar. The
                # person it acts for is not sent: the header is theirs, and the
                # only place a full name can be read.
                agent_name=agent_name,
            )
            if not sent:
                last_error = (
                    UndeliverableReason.COLD_OPEN_UNSUPPORTED
                    if channel.source is None
                    else f"{channel.platform.value} send returned no delivery."
                )
        except (AgentSurfaceError, HTTPError, OSError) as exc:
            # Deliberately NOT bare `Exception`: a bug in our own code must
            # surface rather than be reported as "that channel didn't work".
            last_error = f"{channel.platform.value}: {exc}"
            logger.warning(
                "agent_surfaces.notification_service.channel_send_failed.degraded",
                notification_id=str(notification.id),
                platform=channel.platform.value,
                error=str(exc),
            )
        else:
            if sent and conversation_id is not None:
                # Outside the channel-failure handler: the message is on the
                # platform now, so a failure to record that is not "this
                # channel did not work" -- the row stays SENDING instead.
                notification.mark_delivered(
                    surface_id=channel.surface.id,
                    conversation_id=conversation_id,
                    platform=channel.platform.value,
                )
                return await notifications.update(notification)

    notification.mark_delivery_failed(last_error or "No channel accepted the message.")
    return await notifications.update(notification)


async def _email_budget_refusal(
    channel: DeliveryChannel,
    notification: NotificationEntity,
    rate_limiter: NotificationRateLimiter | None,
) -> str | None:
    """Why this channel may not carry it, when the email budget is spent.

    Charged per email, not per notification: a message that went out on Slack
    costs the shared sending domain nothing. Refusing rather than raising leaves
    the notification in the recipient's inbox with an explanation, which is the
    right outcome for a budget being spent -- the thing is real, we just will
    not mail it.
    """
    if not can_cold_open(channel.surface) or rate_limiter is None:
        return None
    try:
        await rate_limiter.check_email(pod_id=notification.pod_id)
    except EmailSendLimitExceeded as exc:
        return str(exc)
    return None
