"""Telling a person something, durably, wherever they actually are.

The order of operations is the design, and it is deliberately unusual: the row
is written **before** the send is attempted. If the platform call fails the
person still has the notification in Lemma and the asker can still see what it
said. The reverse order puts a message on someone's phone that the pod has no
record of — and a reply to it arrives against nothing.

What this service is *not*: a wait. It never blocks the caller and never
resolves anything back into the calling run. Resolution happens later, in the
recipient's own thread, under the recipient's own authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import settings
from app.core.log.log import get_logger
from httpx import HTTPError

from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceError,
    AgentSurfaceValidationError,
    NotificationNotFoundError,
)
from app.modules.agent_surfaces.domain.events import NotificationSettledEvent
from app.modules.agent_surfaces.domain.notification import (
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.domain.ports import SurfaceNotificationEgressPort
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    UndeliverableReason,
)
from app.modules.agent_surfaces.services.notification_channels import (
    NotificationChannelResolver,
    SurfaceProvisioner,
    can_cold_open,
)
from app.modules.agent_surfaces.services.notification_egress import NotificationEgress
from app.modules.agent_surfaces.services.notification_rate_limiter import (
    EmailSendLimitExceeded,
)

logger = get_logger(__name__)


# Notifications inherit the human wait ceiling rather than inventing a second
# number: 6h machine ceiling x 12 = 72h. A question asked at 18:00 that dies at
# midnight is a worse outcome than one that reads as still waiting, and there is
# no reason for "how long we wait on a person" to mean two different things
# depending on which subsystem asked.
HUMAN_PATIENCE_MULTIPLIER = 12


def default_expiry() -> datetime:
    seconds = settings.workflow_wait_max_age_seconds * HUMAN_PATIENCE_MULTIPLIER
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def attribute(body: str, *, actor_display_name: str | None) -> str:
    """Prefix the message with whose authority it carries, when that is news.

    Not the agent. A surface belongs to exactly one agent and nothing borrows
    another's — see ``surfaces_for_agent`` — so the bot a message arrives from
    *is* the answer to "which agent". Where one app does serve several, the
    platform paints the agent's own name and avatar on the message before it is
    sent (``slack_customized_message_kwargs``). Naming it again in the body adds
    a line and no information, and it was the line that leaked ``pod_default``.

    The actor is the half no channel can carry. Under one bot, with one name and
    one avatar, an agent acting for Priya and the same agent acting for Anukul
    are indistinguishable — and an agent that can message colleagues without
    that distinction is a phishing primitive.

    ``actor_display_name`` is None when the recipient *is* the actor, and that
    is the whole of the omission: the ambiguity this line exists to resolve
    cannot arise when the authority being carried is the reader's own.
    """
    if not actor_display_name:
        return body
    return f"On behalf of {actor_display_name}:\n\n{body}"


class NotificationService:
    """Creates notifications, delivers them, and owns their lifecycle."""

    def __init__(
        self,
        *,
        uow,
        notification_repository,
        surface_repository,
        conversation_link_repository,
        external_user_repository,
        ingress_service: SurfaceNotificationEgressPort,
        pod_membership_port,
        rate_limiter=None,
        surface_provisioner: SurfaceProvisioner | None = None,
    ):
        self.uow = uow
        self.notifications = notification_repository
        self.surfaces = surface_repository
        self.links = conversation_link_repository
        self.external_users = external_user_repository
        self.ingress = ingress_service
        self.membership = pod_membership_port
        self.rate_limiter = rate_limiter
        # Which surface can carry this to this person, and minting one when the
        # agent has none. See ``notification_channels``.
        self.channels = NotificationChannelResolver(
            surface_repository=surface_repository,
            external_user_repository=external_user_repository,
            conversation_link_repository=conversation_link_repository,
            pod_membership_port=pod_membership_port,
            surface_provisioner=surface_provisioner,
        )
        # Getting a message onto a platform is a separate job from owning what
        # the notification means, and it is the half with all the surface
        # coupling. See ``notification_egress``.
        self.egress = NotificationEgress(
            egress=ingress_service,
            uow=uow,
            conversation_link_repository=conversation_link_repository,
        )

    # ------------------------------------------------------------------ create

    async def notify(
        self,
        *,
        pod_id: UUID,
        recipient_user_id: UUID,
        title: str,
        body: str,
        origin_kind: NotificationOriginKind,
        origin_id: UUID | None = None,
        origin_conversation_id: UUID | None = None,
        # The surface this run is answering on, when it is on one. Lets the
        # agent reach out from the same bot the person is already talking to.
        origin_surface_id: UUID | None = None,
        # A channel the agent chose on purpose. Honoured or refused, never
        # swapped: see ``notification_channels._resolve_on_channel``.
        channel: str | None = None,
        actor_user_id: UUID | None = None,
        actor_agent_id: UUID | None = None,
        agent_name: str | None = None,
        background_instruction: str | None = None,
        expects_response: bool = True,
        action: dict | None = None,
        idempotency_key: str | None = None,
        expires_at: datetime | None = None,
        deliver: bool = True,
    ) -> NotificationEntity:
        """Create the row, then try to put it in front of the person.

        Membership FAILS CLOSED. A recipient who is not a member of this pod is
        refused outright rather than quietly skipped, because the alternative —
        a wiring bug that turns into "any user id can be messaged" — is exactly
        the failure this feature must not ship with.
        """
        recipient_pod_member_id = await self.membership.get_pod_member_id(
            recipient_user_id, pod_id
        )
        if recipient_pod_member_id is None:
            raise AgentSurfaceValidationError(
                "The recipient is not a member of this pod."
            )

        if self.rate_limiter is not None:
            await self.rate_limiter.check(
                pod_id=pod_id, recipient_user_id=recipient_user_id
            )

        actor_display_name = (
            await self.membership.get_user_display_name(actor_user_id)
            if actor_user_id
            else None
        )

        notification = await self.notifications.create(
            NotificationEntity(
                pod_id=pod_id,
                recipient_user_id=recipient_user_id,
                recipient_pod_member_id=recipient_pod_member_id,
                actor_user_id=actor_user_id,
                actor_agent_id=actor_agent_id,
                origin_kind=origin_kind,
                origin_id=origin_id,
                origin_conversation_id=origin_conversation_id,
                title=title,
                body=body,
                background_instruction=background_instruction,
                expects_response=expects_response,
                action=action,
                idempotency_key=idempotency_key,
                expires_at=expires_at or default_expiry(),
            )
        )
        # An idempotency hit returns the original, already-delivered row. Sending
        # again is the exact double-post the key exists to prevent.
        if idempotency_key and notification.delivered_at is not None:
            logger.info(
                "agent_surfaces.notification_service.duplicate_suppressed.observed",
                notification_id=str(notification.id),
                pod_id=str(pod_id),
            )
            return notification

        if not deliver:
            return notification

        return await self.deliver(
            notification,
            agent_name=agent_name,
            actor_display_name=actor_display_name,
            origin_surface_id=origin_surface_id,
            channel=channel,
        )

    # ---------------------------------------------------------------- delivery

    async def deliver(
        self,
        notification: NotificationEntity,
        *,
        agent_name: str | None = None,
        actor_display_name: str | None = None,
        origin_surface_id: UUID | None = None,
        channel: str | None = None,
    ) -> NotificationEntity:
        channels, fallback_reason = await self.resolve_channels(
            pod_id=notification.pod_id,
            recipient_user_id=notification.recipient_user_id,
            actor_agent_id=notification.actor_agent_id,
            origin_surface_id=origin_surface_id,
            agent_name=agent_name,
            channel=channel,
        )
        if not channels:
            notification.mark_undeliverable(fallback_reason)
            logger.info(
                "agent_surfaces.notification_service.undeliverable.observed",
                notification_id=str(notification.id),
                reason=fallback_reason,
            )
            return await self.notifications.update(notification)

        # Suppressed for a message to its own asker; still handed to `send`
        # below, which puts it in the email `From` where an inbox list needs
        # *some* name and yours is the true one.
        message = attribute(
            notification.body,
            actor_display_name=(
                None
                if notification.actor_user_id == notification.recipient_user_id
                else actor_display_name
            ),
        )

        # First success wins. Three copies of one message across three apps is
        # how a useful feature comes to read as spam.
        last_error: str | None = None
        for channel in channels:
            try:
                if can_cold_open(channel.surface) and self.rate_limiter is not None:
                    # Charged per email, not per notification: a message that
                    # went out on Slack costs the shared sending domain nothing.
                    # Refusing here rather than raising leaves the notification
                    # in the recipient's inbox with an explanation, which is the
                    # right outcome for a budget being spent — the thing is real,
                    # we just will not mail it.
                    try:
                        await self.rate_limiter.check_email(pod_id=notification.pod_id)
                    except EmailSendLimitExceeded as exc:
                        last_error = str(exc)
                        continue

                conversation_id = await self.egress.conversation_for(
                    channel, notification=notification
                )
                if conversation_id is None:
                    last_error = UndeliverableReason.NEVER_INTERACTED
                    continue

                # Persist before send: a failed platform call must still leave
                # the person a message they can read, and the agent handling
                # their reply a record of what was asked.
                await self.egress.persist_outbound(
                    conversation_id=conversation_id,
                    notification=notification,
                    message=message,
                )
                # Commit before the platform call, for two reasons that point
                # the same way. It makes "persist before send" mean it — an
                # uncommitted row is not a message anyone can read. And it hands
                # the pooled connection back, so the send below does not hold
                # one (nor the row locks it is sitting on) for the seconds a
                # platform API can take. The update after the send opens its own
                # transaction.
                await self.uow.commit()
                sent = await self.egress.send(
                    channel,
                    conversation_id=conversation_id,
                    notification=notification,
                    message=message,
                    # Both names, unconditionally — wider than the body header
                    # above. On email they become the From display name, which
                    # is all an unopened inbox list shows; on chat the agent
                    # name is the bot's username and avatar.
                    agent_name=agent_name,
                    actor_display_name=actor_display_name,
                )
                if not sent:
                    last_error = (
                        UndeliverableReason.COLD_OPEN_UNSUPPORTED
                        if channel.link is None
                        else f"{channel.platform.value} send returned no delivery."
                    )
                    continue

                notification.mark_delivered(
                    surface_id=channel.surface.id,
                    conversation_id=conversation_id,
                    platform=channel.platform.value,
                )
                return await self.notifications.update(notification)
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

        notification.mark_delivery_failed(
            last_error or "No channel accepted the message."
        )
        return await self.notifications.update(notification)

    async def resolve_channels(
        self,
        *,
        pod_id: UUID,
        recipient_user_id: UUID,
        actor_agent_id: UUID | None = None,
        origin_surface_id: UUID | None = None,
        agent_name: str | None = None,
        channel: str | None = None,
    ) -> tuple[list[DeliveryChannel], str]:
        """Every way *this agent* can reach this person, best first.

        Kept as a method because it is part of this service's contract and has
        callers; the routing itself lives in ``notification_channels``.
        """
        return await self.channels.resolve(
            pod_id=pod_id,
            recipient_user_id=recipient_user_id,
            actor_agent_id=actor_agent_id,
            origin_surface_id=origin_surface_id,
            agent_name=agent_name,
            channel=channel,
        )

    async def reachable_channels(
        self,
        *,
        pod_id: UUID,
        recipients: dict[UUID, str | None],
        actor_agent_id: UUID | None = None,
    ) -> dict[UUID, list[str]]:
        """``{user_id: channels}`` — what an agent can choose between, per person."""
        return await self.channels.reachable_channels(
            pod_id=pod_id, recipients=recipients, actor_agent_id=actor_agent_id
        )

    # --------------------------------------------------------------- lifecycle

    async def respond(
        self,
        *,
        pod_id: UUID,
        notification_id: UUID,
        responder_user_id: UUID,
        summary: str,
        data: dict | None = None,
    ) -> NotificationEntity:
        """Record an answer, and say so if it was the last one outstanding.

        The domain decides whether the answer is legal. Whether the asking
        conversation should be brought back is decided here and announced as
        ``NotificationSettledEvent``: this module may not reach into `agent`,
        and the two respond paths used to each have to remember to make that
        call themselves.

        The count is read after the write has been flushed, so the row just
        closed is already counted as settled.
        """
        notification = await self._owned_by(
            pod_id=pod_id,
            notification_id=notification_id,
            user_id=responder_user_id,
        )
        notification.respond(summary=summary, data=data)
        updated = await self.notifications.update(notification)
        await self._announce_if_settled(updated)
        return updated

    async def _announce_if_settled(self, notification: NotificationEntity) -> None:
        """Raise ``NotificationSettledEvent`` once this conversation is owed nothing.

        Scoped to ``AGENT_RUN`` because that is the only origin a conversation
        has: a workflow form is owed to its run, and resuming that is the
        workflow engine's job.
        """
        conversation_id = notification.origin_conversation_id
        if conversation_id is None:
            return
        if notification.origin_kind is not NotificationOriginKind.AGENT_RUN:
            return
        if await self.notifications.count_open_from_origin_conversation(
            conversation_id
        ):
            return
        self.uow.collect_events(
            [
                NotificationSettledEvent(
                    pod_id=notification.pod_id,
                    conversation_id=conversation_id,
                    notification_id=notification.id,
                )
            ]
        )

    async def resolve_through_action(
        self,
        *,
        notification_id: UUID,
        summary: str,
        data: dict | None = None,
    ) -> NotificationEntity | None:
        """Close an action-backed notification from the system owning the action.

        No ownership check: the caller is the workflow engine, which has already
        validated the submission against the node's schema and checked the
        assignee. Returns None when the row is gone or already closed, because a
        run resuming twice must not fail on its second pass.
        """
        notification = await self.notifications.get(notification_id)
        if notification is None or notification.status is not NotificationStatus.OPEN:
            return None
        notification.resolve_through_action(summary=summary, data=data)
        return await self.notifications.update(notification)

    async def acknowledge(
        self, *, pod_id: UUID, notification_id: UUID, user_id: UUID
    ) -> NotificationEntity:
        notification = await self._owned_by(
            pod_id=pod_id, notification_id=notification_id, user_id=user_id
        )
        notification.acknowledge()
        return await self.notifications.update(notification)

    async def mark_read(
        self, *, pod_id: UUID, notification_id: UUID, user_id: UUID
    ) -> NotificationEntity:
        notification = await self._owned_by(
            pod_id=pod_id, notification_id=notification_id, user_id=user_id
        )
        notification.mark_read()
        return await self.notifications.update(notification)

    async def cancel_for_origin(
        self, *, origin_kind: NotificationOriginKind, origin_id: UUID
    ) -> int:
        """Close everything an abandoned run left open behind it."""
        notifications = await self.notifications.list_open_for_origin(
            origin_kind=origin_kind, origin_id=origin_id
        )
        for notification in notifications:
            notification.cancel()
            await self.notifications.update(notification)
        return len(notifications)

    async def expire_past_due(self, *, limit: int = 100) -> int:
        """Sweep notifications nobody answered in time.

        Not a failure — people are busy — but a row that stays OPEN forever is a
        badge that never clears and an asker that waits on nothing.
        """
        expired = 0
        now = datetime.now(timezone.utc)
        for notification in await self.notifications.list_past_due(limit=limit):
            # Re-check rather than catching the entity's refusal: it may have
            # been answered between the query and here, and the answer wins.
            if not notification.is_past_due(now=now):
                continue
            notification.expire()
            await self.notifications.update(notification)
            expired += 1
        return expired

    async def _owned_by(
        self, *, pod_id: UUID, notification_id: UUID, user_id: UUID
    ) -> NotificationEntity:
        """404, not 403, when it belongs to someone else.

        Telling a caller that a notification exists but is not theirs leaks that
        the pod asked that person something, which is exactly the kind of thing
        a notification body is about.
        """
        notification = await self.notifications.get(notification_id)
        if (
            notification is None
            or notification.pod_id != pod_id
            or notification.recipient_user_id != user_id
        ):
            raise NotificationNotFoundError(str(notification_id))
        return notification
