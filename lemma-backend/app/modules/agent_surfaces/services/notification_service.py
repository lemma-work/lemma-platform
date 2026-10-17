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

from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    NotificationNotFoundError,
)
from app.core.infrastructure.work import WorkWriter, facts, pod_namespace
from app.modules.agent_surfaces.contracts.notification_facts import (
    NotificationSettled,
)
from app.modules.agent_surfaces.domain.work_payloads import NotificationExpirePayload
from app.modules.agent_surfaces.events.work_kinds import (
    NOTIFICATION_EXPIRE,
    notification_expire_key,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.domain.ports import SurfaceNotificationEgressPort
from app.modules.agent_surfaces.services.notification_delivery import DeliveryChannel
from app.modules.agent_surfaces.services.channel_lines import (
    ChannelLines,
    ChannelLinesInDatabase,
)
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.agent_surfaces.services.notification_channels import (
    NotificationChannelResolver,
    SurfaceProvisioner,
)
from app.modules.agent_surfaces.services.notification_dispatch import (
    send_on_first_channel,
)
from app.modules.agent_surfaces.services.notification_egress import NotificationEgress
from app.modules.agent_surfaces.services.outside_questions import (
    MAX_OUTSIDE_QUESTION_CHARS,
    OutsideOriginReader,
    outside_question_message,
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


class NotificationWork:
    """The work a notification writes on its unit of work: its expiry, its settling.

    A collaborator rather than calls inline, so a test of what the service
    decides records these instead of needing the work tables.
    """

    async def arm_expiry(self, uow, notification: NotificationEntity) -> None:
        await _arm_expiry(uow, notification)

    async def settled(self, uow, fact: NotificationSettled) -> None:
        await facts.emit(uow, fact)


class NotificationService:
    """Creates notifications, delivers them, and owns their lifecycle."""

    def __init__(
        self,
        *,
        uow,
        notification_repository,
        surface_repository,
        external_user_repository,
        egress: SurfaceNotificationEgressPort,
        pod_membership_port,
        outside_origin_reader: OutsideOriginReader,
        rate_limiter=None,
        surface_provisioner: SurfaceProvisioner | None = None,
        work: NotificationWork | None = None,
        seats: ChannelSeats | None = None,
        lines: ChannelLines | None = None,
    ):
        self.uow = uow
        self.work = work or NotificationWork()
        # Required, so no way of building this service can forget it: it is
        # what marks a question passed on from outside the pod, and an unmarked
        # one would have its answer recorded without the recipient's say-so.
        self.outside_origin = outside_origin_reader
        self.notifications = notification_repository
        # The three repositories this used to also hang on `self` are not kept,
        # and neither is the egress collaborator: all four were assigned and
        # never read, because the objects that use them are built with them
        # below. A field nothing reads reads as a seam -- someone will reach for
        # `service.surfaces` and get a repository this class has no opinion
        # about, or for `service.ingress` and get the whole inbound service.
        self.membership = pod_membership_port
        self.rate_limiter = rate_limiter
        # Which surface can carry this to this person, and minting one when the
        # agent has none. See ``notification_channels``.
        seats = seats or ChannelSeatsInDatabase(uow)
        self.channels = NotificationChannelResolver(
            seats=seats,
            surface_repository=surface_repository,
            external_user_repository=external_user_repository,
            pod_membership_port=pod_membership_port,
            surface_provisioner=surface_provisioner,
        )
        # Getting a message onto a platform is a separate job from owning what
        # the notification means, and it is the half with all the surface
        # coupling. See ``notification_egress``.
        self.egress = NotificationEgress(
            egress=egress,
            uow=uow,
            seats=seats,
            lines=lines or ChannelLinesInDatabase(uow),
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
        # From the asking conversation's routing link, not from anything the
        # asking run said: a question passed on from outside the pod is one
        # whose answer is only recorded as words the recipient approved.
        outside = await self.outside_origin(origin_conversation_id)
        if outside is not None and len(body) > MAX_OUTSIDE_QUESTION_CHARS:
            body = body[:MAX_OUTSIDE_QUESTION_CHARS]

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
                from_outside=outside is not None,
                origin_group_title=outside.group_title if outside else None,
                asked_in_private=outside.in_private if outside else False,
                asked_by_name=outside.asked_by_name if outside else None,
                title=title,
                body=body,
                background_instruction=background_instruction,
                expects_response=expects_response,
                action=action,
                idempotency_key=idempotency_key,
                expires_at=expires_at or default_expiry(),
            )
        )
        await self.work.arm_expiry(self.uow, notification)
        # An idempotency hit returns the original row. Sending again is the
        # exact double-post the key exists to prevent -- whether it was
        # delivered, or its send was started and never recorded, which is when
        # the platform may well have it and only the bookkeeping failed.
        if idempotency_key and notification.send_outcome_unknown:
            logger.warning(
                "agent_surfaces.notification_service.send_outcome_unknown.degraded",
                notification_id=str(notification.id),
                pod_id=str(pod_id),
            )
            return notification
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
        message = (
            # A stranger's words, framed and quoted by the server: they reach
            # the member's own thread, where an agent with all of the member's
            # access reads them.
            outside_question_message(notification, agent_name=agent_name)
            if notification.from_outside
            else attribute(
                notification.body,
                actor_display_name=(
                    None
                    if notification.actor_user_id == notification.recipient_user_id
                    else actor_display_name
                ),
            )
        )

        return await send_on_first_channel(
            notification,
            channels=channels,
            message=message,
            agent_name=agent_name,
            uow=self.uow,
            notifications=self.notifications,
            egress=self.egress,
            rate_limiter=self.rate_limiter,
        )

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
        owner_confirmed: bool = False,
    ) -> NotificationEntity:
        """Record an answer, and say so if it was the last one outstanding.

        ``owner_confirmed`` is the recipient's own say-so on these exact words:
        they typed them, or approved them as their agent drafted them. A
        question from outside the pod is answered only with it.

        The domain decides whether the answer is legal. Whether the asking
        conversation should be brought back is decided here and announced as
        the ``NotificationSettled`` fact, in this transaction: this module may
        not reach into `agent`, and the agent's subscriber item commits with the
        answer -- an answer can no longer be recorded and then lost.

        The count is read after the write has been flushed, so the row just
        closed is already counted as settled.
        """
        notification = await self._owned_by(
            pod_id=pod_id,
            notification_id=notification_id,
            user_id=responder_user_id,
        )
        notification.respond(
            summary=summary, data=data, owner_confirmed=owner_confirmed
        )
        updated = await self.notifications.update(notification)
        await self._announce_if_settled(updated)
        return updated

    async def _announce_if_settled(self, notification: NotificationEntity) -> None:
        await announce_if_settled(
            self.uow, self.notifications, notification, work=self.work
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

    async def is_from_outside(
        self, *, pod_id: UUID, notification_id: UUID, user_id: UUID
    ) -> bool:
        """Whether this person's notification is a question from outside the pod.

        False for one that is not theirs, rather than an answer either way: the
        respond that follows refuses it as not found.
        """
        notification = await self.notifications.get(notification_id)
        return (
            notification is not None
            and notification.pod_id == pod_id
            and notification.recipient_user_id == user_id
            and notification.from_outside
        )

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


async def announce_if_settled(
    uow,
    notifications,
    notification: NotificationEntity,
    *,
    work: NotificationWork | None = None,
) -> None:
    """Emit ``NotificationSettled`` once this conversation is owed nothing.

    Scoped to ``AGENT_RUN`` because that is the only origin a conversation
    has: a workflow form is owed to its run, and resuming that is the workflow
    engine's job. The count is read after the write has been flushed, so the
    row just closed is already counted as settled.
    """
    conversation_id = notification.origin_conversation_id
    if conversation_id is None or not notification.expects_response:
        # An FYI closes nothing the asker was waiting on, so its expiry is
        # not news either.
        return
    if notification.origin_kind is not NotificationOriginKind.AGENT_RUN:
        return
    if await notifications.count_open_from_origin_conversation(conversation_id):
        return
    await (work or NotificationWork()).settled(
        uow,
        NotificationSettled(
            conversation_id=conversation_id, pod_id=notification.pod_id
        ),
    )


async def _arm_expiry(uow, notification: NotificationEntity) -> None:
    """The item that expires this notification if nobody answers in time.

    Written with the row (an idempotency hit re-arms nothing: the dedup key
    coalesces), so no sweep has to find past-due rows.
    """
    if notification.id is None or notification.expires_at is None:
        return
    if notification.status is not NotificationStatus.OPEN:
        return
    await WorkWriter.of(uow).add(
        NOTIFICATION_EXPIRE,
        pod_namespace(notification.pod_id),
        NotificationExpirePayload(notification_id=notification.id),
        dedup_key=notification_expire_key(notification.id),
        run_at=notification.expires_at,
    )


async def expire_one(
    uow,
    notifications,
    notification_id: UUID,
    *,
    work: NotificationWork | None = None,
) -> bool:
    """Expire one notification if it is still open and past due. True if it was.

    The answer wins a race: a notification answered meanwhile is left alone.
    """
    notification = await notifications.get(notification_id)
    if notification is None or notification.status is not NotificationStatus.OPEN:
        return False
    if not notification.is_past_due(now=datetime.now(timezone.utc)):
        return False
    notification.expire()
    await announce_if_settled(
        uow, notifications, await notifications.update(notification), work=work
    )
    return True
