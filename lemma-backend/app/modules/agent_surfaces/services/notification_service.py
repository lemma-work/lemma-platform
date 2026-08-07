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
from app.modules.agent.domain.value_objects import MessageDraft
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceMode,
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
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    UndeliverableReason,
    rank_candidates,
    reply_window_open,
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

    # A conversation touched more recently than this is still "the thread we are
    # in"; anything colder starts a new one. Much tighter than the surface's 24h
    # DM reset on purpose — that setting exists to stop threads living forever,
    # not to decide whether a new subject belongs in an old one. A standup ping
    # arriving the morning after yesterday's support chat is a new subject, and
    # appending it there reads as a non-sequitur.
    CONTINUE_CONVERSATION_WITHIN = timedelta(minutes=30)

    def __init__(
        self,
        *,
        uow,
        notification_repository,
        surface_repository,
        conversation_link_repository,
        external_user_repository,
        conversation_service,
        ingress_service,
        pod_membership_port,
        rate_limiter=None,
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
        )

    # ---------------------------------------------------------------- delivery

    async def deliver(
        self,
        notification: NotificationEntity,
        *,
        agent_name: str | None = None,
        actor_display_name: str | None = None,
    ) -> NotificationEntity:
        channels, fallback_reason = await self.resolve_channels(
            pod_id=notification.pod_id,
            recipient_user_id=notification.recipient_user_id,
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
                conversation_id = await self._conversation_for(
                    channel, notification=notification
                )
                if conversation_id is None:
                    last_error = UndeliverableReason.NEVER_INTERACTED
                    continue

                # Persist before send: a failed platform call must still leave
                # the person a message they can read, and the agent handling
                # their reply a record of what was asked.
                await self._persist_outbound(
                    conversation_id=conversation_id,
                    notification=notification,
                    message=message,
                )
                sent = await self._send(
                    channel,
                    conversation_id=conversation_id,
                    notification=notification,
                    message=message,
                )
                if not sent:
                    last_error = f"{channel.platform.value} send returned no delivery."
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

    async def _send(
        self,
        channel: DeliveryChannel,
        *,
        conversation_id: UUID,
        notification: NotificationEntity,
        message: str,
    ) -> bool:
        """Hand the message to the platform.

        The fork is not chat-vs-email, it is *do we have a thread*. Every normal
        egress path replies to a stored inbound event; a cold open has none, so
        it addresses the mailbox directly. That is only possible on platforms
        whose ``can_cold_open`` says so, which is why a chat channel never
        reaches the second branch — it would have had no candidate without a
        link in the first place.
        """
        metadata = {"notification_id": str(notification.id)}
        if channel.link is not None:
            return await self.ingress.send_agent_message_for_conversation(
                conversation_id=conversation_id,
                message=message,
                metadata=metadata,
            )
        if not channel.email_address:
            return False
        return await self.ingress.send_cold_email(
            surface=channel.surface,
            recipient_email=channel.email_address,
            subject=notification.title,
            message=message,
            metadata=metadata,
        )

    async def resolve_channels(
        self, *, pod_id: UUID, recipient_user_id: UUID
    ) -> tuple[list[DeliveryChannel], str]:
        """Every way this pod can reach this person, best first.

        Returns the reason alongside, so an empty list is never a silent no.
        """
        # ``list_by_pod`` is paginated and returns ``(items, next_cursor)``. A pod
        # with more surfaces than one page is not a real shape — they are bots a
        # human connected by hand — so the first page is the whole set.
        all_surfaces, _ = await self.surfaces.list_by_pod(pod_id)
        surfaces = [surface for surface in all_surfaces if surface.is_active]
        if not surfaces:
            return [], UndeliverableReason.NO_ACTIVE_SURFACE

        preferred = set(
            await self.membership.get_user_default_surface_ids(recipient_user_id)
        )

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
            external = await self.external_users.get_by_resolved_user(
                platform=surface.surface_type.value,
                resolved_user_id=recipient_user_id,
            )
            if external is None or not external.external_user_id:
                continue
            link = await self.links.get_latest_by_surface_and_external_user(
                surface_id=surface.id,
                external_user_id=external.external_user_id,
            )
            if link is None:
                continue
            if not reply_window_open(
                platform=surface.surface_type,
                last_inbound_at=link.inbound_activity_at,
            ):
                window_closed = True
                continue
            candidates.append(
                DeliveryChannel(
                    surface=surface,
                    external_user_id=external.external_user_id,
                    link=link,
                )
            )

        if candidates:
            return rank_candidates(candidates, preferred_surface_ids=preferred), ""

        # Reasons are ordered by how actionable they are. "Their window closed"
        # tells someone to wait; "they never messaged the bot" tells them what to
        # go and do, and is the one that will actually be hit in practice.
        if window_closed:
            return [], UndeliverableReason.REPLY_WINDOW_CLOSED
        if saw_chat_surface:
            return [], UndeliverableReason.NEVER_INTERACTED
        return [], UndeliverableReason.NO_EMAIL_ADDRESS

    async def _email_channel(
        self, surface: AgentSurfaceEntity, recipient_user_id: UUID
    ) -> DeliveryChannel | None:
        address = await self.membership.get_user_email(recipient_user_id)
        if not address:
            return None
        return DeliveryChannel(surface=surface, email_address=address)

    async def _conversation_for(
        self, channel: DeliveryChannel, *, notification: NotificationEntity
    ) -> UUID | None:
        """The recipient's conversation this notification lands in.

        A live thread is continued; anything colder opens a new conversation and
        repoints the link with a compare-and-set, so an inbound arriving in the
        same instant cannot leave one platform thread split across two
        conversations.

        A message you cannot reply to is an alert, not a conversation — which is
        why this always resolves to a conversation the *recipient* owns, never
        the asker's. Their reply runs under their own permissions.
        """
        if channel.link is None:
            # Cold-open email: no prior thread exists by definition.
            return await self._open_conversation(channel, notification=notification)

        conversation = await self.conversation_service.conversation_repository.get_conversation(
            channel.link.conversation_id
        )
        if conversation is not None:
            touched = conversation.updated_at
            if touched.tzinfo is None:
                touched = touched.replace(tzinfo=timezone.utc)
            if (
                datetime.now(timezone.utc) - touched
                <= self.CONTINUE_CONVERSATION_WITHIN
            ):
                return conversation.id

        opened = await self._open_conversation(channel, notification=notification)
        if opened is None:
            return channel.link.conversation_id
        repointed = await self.links.repoint_conversation_for_outbound(
            link_id=channel.link.id,
            conversation_id=opened,
            expected_conversation_id=channel.link.conversation_id,
        )
        if repointed is None:
            # An inbound won the race and already repointed the link. Deliver
            # into the conversation it created rather than stealing the thread.
            current = await self.links.get_by_conversation_id(opened)
            logger.info(
                "agent_surfaces.notification_service.link_repoint_lost_race.observed",
                notification_id=str(notification.id),
                link_id=str(channel.link.id),
            )
            return current.conversation_id if current else opened
        return opened

    async def _open_conversation(
        self, channel: DeliveryChannel, *, notification: NotificationEntity
    ) -> UUID | None:
        surface = channel.surface
        conversation = await self.conversation_service.create_conversation(
            pod_id=notification.pod_id,
            agent_name=await self.ingress.agent_name_for_surface(surface),
            user_id=notification.recipient_user_id,
            title=notification.title,
            metadata={
                "source": "notification",
                "notification_id": str(notification.id),
                "surface_id": str(surface.id),
                "surface_platform": surface.surface_type.value,
                "external_user_id": channel.external_user_id,
                "conversation_kind": (
                    "EMAIL" if surface.mode is SurfaceMode.EMAIL else "DM"
                ),
            },
            require_execute_grant=False,
        )
        return conversation.id if conversation else None

    async def _persist_outbound(
        self,
        *,
        conversation_id: UUID,
        notification: NotificationEntity,
        message: str,
    ) -> None:
        """Write the outbound into the recipient's thread before it is sent.

        ``MessageKind.NOTIFICATION`` already exists and is already replayed into
        model history, so this costs a write rather than a migration — and it is
        what makes the recipient's agent able to answer "yes" six hours later
        against a question it can actually see.
        """
        await self.conversation_service.conversation_repository.append_message(
            conversation_id=conversation_id,
            agent_run_id=None,
            draft=MessageDraft.of_notification(
                message,
                metadata={"notification_id": str(notification.id)},
            ),
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
