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

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import settings
from app.modules.agent_surfaces.config import (
    resolve_resend_api_key,
    surface_settings,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
)
from httpx import HTTPError

from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceError,
    AgentSurfaceValidationError,
    NotificationNotFoundError,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)
from app.modules.agent_surfaces.domain.ports import SurfaceNotificationEgressPort
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    UndeliverableReason,
    rank_candidates,
    reply_window_open,
    surfaces_for_agent,
)
from app.modules.agent_surfaces.services.notification_egress import NotificationEgress

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


def attribute(
    body: str, *, agent_name: str | None, actor_display_name: str | None
) -> str:
    """Prefix the message with who is asking and on whose authority.

    Mandatory, not cosmetic. The recipient sees the pod's bot — the same bot
    they trust — and has no other way to tell "the agent Priya's schedule is
    running" from "the pod". Without this line, an agent that can message
    colleagues is a phishing primitive.
    """
    if agent_name and actor_display_name:
        header = f"*{agent_name}*, on behalf of {actor_display_name}:"
    elif agent_name:
        header = f"*{agent_name}*:"
    elif actor_display_name:
        header = f"On behalf of {actor_display_name}:"
    else:
        return body
    return f"{header}\n\n{body}"


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
        conversation_service,
        ingress_service: SurfaceNotificationEgressPort,
        pod_membership_port,
        rate_limiter=None,
        surface_provisioner: (
            Callable[[UUID], Awaitable[AgentSurfaceEntity | None]] | None
        ) = None,
    ):
        self.uow = uow
        self.notifications = notification_repository
        self.surfaces = surface_repository
        self.links = conversation_link_repository
        self.external_users = external_user_repository
        self.conversation_service = conversation_service
        self.ingress = ingress_service
        self.membership = pod_membership_port
        self.rate_limiter = rate_limiter
        # Injected rather than built here: creating a surface is the surface
        # service's job, and it already owns address provisioning, credential
        # checks and receiver registration.
        self.surface_provisioner = surface_provisioner
        # Getting a message onto a platform is a separate job from owning what
        # the notification means, and it is the half with all the surface
        # coupling. See ``notification_egress``.
        self.egress = NotificationEgress(
            egress=ingress_service,
            conversation_service=conversation_service,
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
        )

    # ---------------------------------------------------------------- delivery

    async def deliver(
        self,
        notification: NotificationEntity,
        *,
        agent_name: str | None = None,
        actor_display_name: str | None = None,
        origin_surface_id: UUID | None = None,
    ) -> NotificationEntity:
        channels, fallback_reason = await self.resolve_channels(
            pod_id=notification.pod_id,
            recipient_user_id=notification.recipient_user_id,
            actor_agent_id=notification.actor_agent_id,
            origin_surface_id=origin_surface_id,
        )
        if not channels:
            notification.mark_undeliverable(fallback_reason)
            logger.info(
                "agent_surfaces.notification_service.undeliverable.observed",
                notification_id=str(notification.id),
                reason=fallback_reason,
            )
            return await self.notifications.update(notification)

        message = attribute(
            notification.body,
            agent_name=agent_name,
            actor_display_name=actor_display_name,
        )

        # First success wins. Three copies of one message across three apps is
        # how a useful feature comes to read as spam.
        last_error: str | None = None
        for channel in channels:
            try:
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
                sent = await self.egress.send(
                    channel,
                    conversation_id=conversation_id,
                    notification=notification,
                    message=message,
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
    ) -> tuple[list[DeliveryChannel], str]:
        """Every way *this agent* can reach this person, best first.

        Scoped to the sending agent's own surfaces rather than the pod's: the
        recipient should hear from the bot they already know this agent as. See
        ``notification_delivery`` for why that beats the recipient's own
        preferred surface.

        Returns the reason alongside, so an empty list is never a silent no.
        """
        # ``list_by_pod`` is paginated and returns ``(items, next_cursor)``. A pod
        # with more surfaces than one page is not a real shape — they are bots a
        # human connected by hand — so the first page is the whole set.
        all_surfaces, _ = await self.surfaces.list_by_pod(pod_id)
        active = [surface for surface in all_surfaces if surface.is_active]
        if not active:
            # A pod with no surface can reach nobody, and asking someone to go
            # and connect one before their first notification will send is a
            # poor trade when we already run a system mailbox. Provisioned
            # lazily rather than at pod creation: most pods never message
            # anyone, and an address handed out is an address to keep working.
            provisioned = await self._auto_provision_email_surface(pod_id)
            if provisioned is None:
                return [], UndeliverableReason.NO_ACTIVE_SURFACE
            active = [provisioned]

        surfaces = surfaces_for_agent(active, actor_agent_id=actor_agent_id)
        if not surfaces:
            return [], UndeliverableReason.NO_SURFACE_FOR_AGENT

        candidates: list[DeliveryChannel] = []
        saw_chat_surface = False
        window_closed = False

        for surface in surfaces:
            capabilities = get_platform_capabilities(surface.surface_type.value)
            if capabilities is not None and capabilities.can_cold_open:
                # No prior thread needed: the address *is* the channel.
                email_channel = await self._email_channel(surface, recipient_user_id)
                if email_channel is not None:
                    candidates.append(email_channel)
                continue

            saw_chat_surface = True
            channel, closed = await self._chat_channel(surface, recipient_user_id)
            window_closed = window_closed or closed
            if channel is not None:
                candidates.append(channel)

        if candidates:
            return rank_candidates(
                candidates, origin_surface_id=origin_surface_id
            ), ""

        # Reasons are ordered by how actionable they are. "Their window closed"
        # tells someone to wait; "they never messaged the bot" tells them what to
        # go and do, and is the one that will actually be hit in practice.
        if window_closed:
            return [], UndeliverableReason.REPLY_WINDOW_CLOSED
        if saw_chat_surface:
            return [], UndeliverableReason.NEVER_INTERACTED
        return [], UndeliverableReason.NO_EMAIL_ADDRESS

    async def _auto_provision_email_surface(
        self, pod_id: UUID,
    ) -> AgentSurfaceEntity | None:
        """Give this pod the system mailbox, or None if that is not on offer.

        Off unless configured, and silent when it fails. A notification that
        cannot be delivered is already a handled outcome — the row exists and
        the inbox has it — so a provisioning problem must degrade to
        ``NO_ACTIVE_SURFACE`` rather than turn a send into an exception.
        """
        if not (
            surface_settings.resend_auto_provision_enabled
            and resolve_resend_api_key()
        ):
            return None
        if self.surface_provisioner is None:
            return None
        try:
            return await self.surface_provisioner(pod_id)
        except (AgentSurfaceError, OSError) as exc:
            logger.warning(
                "agent_surfaces.notification_service.auto_provision_failed.degraded",
                pod_id=str(pod_id),
                error=str(exc),
            )
            return None

    async def _chat_channel(
        self, surface: AgentSurfaceEntity, recipient_user_id: UUID
    ) -> tuple[DeliveryChannel | None, bool]:
        """``(channel, window_closed)`` for a chat surface.

        A bot cannot start a conversation, so this only yields a channel when
        the person has written to that surface before. ``window_closed`` is
        reported separately because "they went quiet too long ago" is a
        different thing to tell somebody than "they have never messaged us".
        """
        external = await self.external_users.get_by_resolved_user(
            platform=surface.surface_type.value,
            resolved_user_id=recipient_user_id,
        )
        if external is None or not external.external_user_id:
            return None, False
        link = await self.links.get_latest_by_surface_and_external_user(
            surface_id=surface.id,
            external_user_id=external.external_user_id,
        )
        if link is None:
            return None, False
        if not reply_window_open(
            platform=surface.surface_type,
            last_inbound_at=link.inbound_activity_at,
        ):
            return None, True
        return (
            DeliveryChannel(
                surface=surface,
                external_user_id=external.external_user_id,
                link=link,
            ),
            False,
        )

    async def _email_channel(
        self, surface: AgentSurfaceEntity, recipient_user_id: UUID
    ) -> DeliveryChannel | None:
        address = await self.membership.get_user_email(recipient_user_id)
        if not address:
            return None
        return DeliveryChannel(surface=surface, email_address=address)

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
        """Record an answer. The domain decides whether it is legal."""
        notification = await self._owned_by(
            pod_id=pod_id,
            notification_id=notification_id,
            user_id=responder_user_id,
        )
        notification.respond(summary=summary, data=data)
        return await self.notifications.update(notification)

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
