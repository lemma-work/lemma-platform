"""Everything between "we picked a channel" and "the platform has it".

Split out of ``notification_service`` so that file stays under the size ceiling,
but the seam is a real one rather than a filing convenience: this half talks to
surfaces and conversations, the other half owns notification *state* — creation,
response, expiry — and never needs to know how a message physically leaves.

The collaborator is held as :class:`SurfaceNotificationEgressPort`, not as an
untyped kwarg. That is not decoration. Both of the ``AttributeError`` crashes
this module was extracted to fix were calls to ingress methods that had never
been written, and neither mypy nor any test could see them because the
attribute was untyped and every test ran in a pod with no surface, where
delivery returns before reaching this code at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import MessageDraft
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    SurfaceMode,
)
from app.modules.agent_surfaces.domain.notification import NotificationEntity
from app.modules.agent_surfaces.domain.ports import (
    ColdEmailThread,
    SurfaceNotificationEgressPort,
)
from app.modules.agent_surfaces.services.cold_email_thread import cold_thread_seed_id
from app.modules.agent_surfaces.services.notification_delivery import DeliveryChannel

logger = get_logger(__name__)


class NotificationEgress:
    """Resolves the conversation a notification lands in, and sends it."""

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
        egress: SurfaceNotificationEgressPort,
        conversation_service,
        conversation_link_repository,
    ) -> None:
        self.egress = egress
        self.conversation_service = conversation_service
        self.links = conversation_link_repository

    async def send(
        self,
        channel: DeliveryChannel,
        *,
        conversation_id: UUID,
        notification: NotificationEntity,
        message: str,
        agent_name: str | None = None,
        actor_display_name: str | None = None,
    ) -> bool:
        """Hand the message to the platform.

        The fork is not chat-vs-email, it is *do we have a thread*. Every normal
        egress path replies to a stored inbound event; a cold open has none, so
        it addresses the mailbox directly. That is only possible on platforms
        whose ``can_cold_open`` says so, which is why a chat channel never
        reaches the second branch — it would have had no candidate without a
        link in the first place.

        Both names travel in the metadata because email puts them in the ``From``
        display name, where an inbox list will actually show them — the body
        header from ``attribute()`` is not visible until the message is opened.
        A chat platform ignores them; its bot identity is the surface's, and the
        body header is the whole attribution it gets.
        """
        metadata: dict[str, Any] = {"notification_id": str(notification.id)}
        # Set only when known. ``_egress_metadata_with_agent_name`` fills
        # ``agent_display_name`` from the surface with ``setdefault``, and an
        # explicit None here is a present key — it would win, and every chat
        # bot would lose the name and icon it replies under.
        if agent_name:
            metadata["agent_display_name"] = agent_name
        if actor_display_name:
            metadata["actor_display_name"] = actor_display_name
        if channel.link is not None:
            return await self.egress.send_agent_message_for_conversation(
                conversation_id=conversation_id,
                message=message,
                metadata=metadata,
            )
        if not channel.email_address:
            return False
        return await self._send_cold_email(
            channel,
            conversation_id=conversation_id,
            notification=notification,
            message=message,
            metadata=metadata,
        )

    async def _send_cold_email(
        self,
        channel: DeliveryChannel,
        *,
        conversation_id: UUID,
        notification: NotificationEntity,
        message: str,
        metadata: dict[str, Any],
    ) -> bool:
        """First contact by email, then remember the thread it opened.

        The link is written *after* the send, not before, because some providers
        only reveal the thread id once the mail is away — and a link pointing at
        a thread that does not exist would swallow the person's reply into a
        conversation nothing ever delivered to.
        """
        assert channel.email_address is not None  # guarded by the caller
        thread = await self.egress.open_cold_email_thread(
            surface=channel.surface,
            recipient_email=channel.email_address,
            subject=notification.title,
            message=message,
            thread_seed_id=cold_thread_seed_id(
                notification_id=notification.id, surface=channel.surface
            ),
            metadata=metadata,
        )
        if thread is None:
            # Not a failure to retry elsewhere: this platform replies through an
            # endpoint keyed by a message id it does not have.
            logger.info(
                "agent_surfaces.notification_egress.cold_open_unsupported.observed",
                notification_id=str(notification.id),
                platform=channel.surface.surface_type.value,
            )
            return False
        await self._remember_thread(
            thread, channel=channel, conversation_id=conversation_id
        )
        return True

    async def _remember_thread(
        self,
        thread: ColdEmailThread,
        *,
        channel: DeliveryChannel,
        conversation_id: UUID,
    ) -> None:
        """Record the thread so the reply resolves to this conversation.

        Get-then-create rather than a blind insert: the seed is derived from the
        notification id, so a re-delivered notification arrives here with a link
        already written and must reuse it instead of tripping the unique index.
        """
        surface = channel.surface
        existing = await self.links.get_by_external_thread(
            surface_id=surface.id,
            platform=surface.surface_type.value,
            external_channel_id=thread.external_channel_id,
            external_thread_id=thread.external_thread_id,
            external_user_id=(channel.email_address or "").strip().lower() or None,
        )
        if existing is not None:
            return
        await self.links.create(
            AgentSurfaceConversationLink(
                surface_id=surface.id,
                conversation_id=conversation_id,
                platform=surface.surface_type.value,
                external_channel_id=thread.external_channel_id,
                external_thread_id=thread.external_thread_id,
                external_user_id=(channel.email_address or "").strip().lower() or None,
                conversation_kind="EMAIL",
                last_event=thread.last_event,
                last_message_id=thread.external_message_id,
                # They have not written to us. Claiming otherwise would let an
                # outbound masquerade as inbound activity in channel ranking.
                last_inbound_at=None,
            )
        )

    async def conversation_for(
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
            return await self.open_conversation(channel, notification=notification)

        conversation = (
            await self.conversation_service.conversation_repository.get_conversation(
                channel.link.conversation_id
            )
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

        opened = await self.open_conversation(channel, notification=notification)
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
                "agent_surfaces.notification_egress.link_repoint_lost_race.observed",
                notification_id=str(notification.id),
                link_id=str(channel.link.id),
            )
            return current.conversation_id if current else opened
        return opened

    async def persist_outbound(
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

    async def open_conversation(
        self, channel: DeliveryChannel, *, notification: NotificationEntity
    ) -> UUID | None:
        surface = channel.surface
        conversation = await self.conversation_service.create_conversation(
            pod_id=notification.pod_id,
            agent_name=await self.egress.agent_name_for_surface(surface),
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


__all__ = ["NotificationEgress"]
