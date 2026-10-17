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

from typing import Any
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id, is_pod_default_agent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.notification import NotificationEntity
from app.modules.agent_surfaces.domain.ports import (
    ColdEmailThread,
    SurfaceNotificationEgressPort,
)
from app.modules.agent_surfaces.services.cold_email_thread import cold_thread_seed_id
from app.modules.agent_surfaces.services.group_names import sender_name_for
from app.modules.agent_surfaces.services.channel_lines import ChannelLines
from app.modules.agent_surfaces.services.channel_places import ChannelSeats
from app.modules.agent_surfaces.services.notification_delivery import DeliveryChannel
from app.modules.channels.contracts import (
    MembersHome,
    Place,
    Principal,
    Sender,
    SourceKind,
)

logger = get_logger(__name__)


class NotificationEgress:
    """Resolves the conversation a notification lands in, and sends it."""

    def __init__(
        self,
        *,
        egress: SurfaceNotificationEgressPort,
        uow: SqlAlchemyUnitOfWork,
        seats: ChannelSeats,
        lines: ChannelLines,
    ) -> None:
        self.egress = egress
        self.uow = uow
        self.seats = seats
        self.lines = lines

    async def send(
        self,
        channel: DeliveryChannel,
        *,
        conversation_id: UUID,
        notification: NotificationEntity,
        message: str,
        agent_name: str | None = None,
    ) -> bool:
        """Hand the message to the platform.

        The fork is not chat-vs-email, it is *do we have a thread*. Every normal
        egress path replies to a stored inbound event; a cold open has none, so
        it addresses the mailbox directly. That is only possible on platforms
        whose ``can_cold_open`` says so, which is why a chat channel never
        reaches the second branch — it would have had no candidate without a
        place in the first place.

        The agent's name travels in the metadata because email puts it in the
        ``From`` display name, where an inbox list will actually show it, and a
        chat platform takes it as the bot's username and avatar. The person the
        run acts for does not: ``attribute()`` puts them in the body, which is
        the only place a full name can be read whole.
        """
        metadata: dict[str, Any] = {"notification_id": str(notification.id)}
        # Set only when known. ``SurfaceDelivery.egress_metadata`` fills
        # ``agent_display_name`` from the surface with ``setdefault``, and an
        # explicit None here is a present key — it would win, and every chat
        # bot would lose the name and icon it replies under.
        if agent_name:
            metadata["agent_display_name"] = agent_name
        if channel.source is not None:
            return await self.egress.send_agent_message_for_conversation(
                conversation_id=conversation_id,
                message=message,
                metadata=metadata,
                at=channel.source,
            )
        if not channel.email_address:
            return False
        # The name this mail wears in the ``From`` header. Cold-open mail is the
        # one send that never passes through ``egress_metadata``, which fills
        # the same key on the reply path — and it is not the agent's display
        # name, because the pod's own assistant goes out under the pod's name.
        is_pod_default = is_pod_default_agent(
            channel.surface.agent_id, pod_id=channel.surface.pod_id
        )
        # A caller may name the run's agent or not; the surface chosen is a
        # named agent's either way, and mail from it wears that agent's name
        # rather than the deployment's.
        if not is_pod_default and agent_name is None:
            agent_name = await self.egress.agent_name_for_surface(channel.surface)
        metadata["email_sender_name"] = await sender_name_for(
            self.uow,
            is_pod_default=is_pod_default,
            agent_name=agent_name,
            pod_id=channel.surface.pod_id,
        )
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

        The place is made *after* the send, not before, because some providers
        only reveal the thread id once the mail is away — and a place for a
        thread that does not exist would swallow the person's reply into a
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
            thread,
            channel=channel,
            conversation_id=conversation_id,
            notification=notification,
            message=message,
        )
        return True

    async def _remember_thread(
        self,
        thread: ColdEmailThread,
        *,
        channel: DeliveryChannel,
        conversation_id: UUID,
        notification: NotificationEntity,
        message: str,
    ) -> None:
        """The thread as a place in the recipient's channel, and the mail as
        its first line, so their reply continues this conversation.

        The place is keyed as their reply will arrive -- the thread and their
        address -- and made only if new, since a re-delivered notification
        reuses the seed and so the thread. Its address is the thread's opening
        mail: nothing has been heard there yet.
        """
        surface = channel.surface
        address = (channel.email_address or "").strip().lower() or None
        source = await self.seats.source_for(
            pod_id=notification.pod_id,
            place=Place(
                surface_id=surface.id,
                platform=surface.surface_type.value,
                kind=SourceKind.PRIVATE,
                external_channel_id=thread.external_thread_id,
                external_user_id=address,
            ),
            home=MembersHome(user_id=notification.recipient_user_id),
        )
        await self.seats.addressed_at(source.id, thread.last_event)
        await self.lines.opened(
            source=source,
            sender=Sender.agent(
                effective_agent_id(surface.agent_id, pod_id=notification.pod_id),
                on_behalf_of=Principal.member(notification.recipient_user_id),
            ),
            text=message,
            conversation_id=conversation_id,
            external_message_id=thread.external_message_id,
            external_thread_id=thread.external_thread_id,
        )

    async def conversation_for(
        self, channel: DeliveryChannel, *, notification: NotificationEntity
    ) -> UUID | None:
        """The recipient's conversation this notification lands in.

        Their one conversation with the sending agent in the channel the
        message goes to -- the chat's, or for a cold email their DIRECT
        channel -- opened if they have none. However cold, it is the same
        conversation: the agent sees the question beside everything else
        between them, and a reply, a tap on its buttons or a typed answer
        reaches it.

        Always a conversation the *recipient* owns, never the asker's, so their
        reply runs under their own permissions.
        """
        pod_id = notification.pod_id
        recipient = notification.recipient_user_id
        channel_id = (
            channel.source.channel_id
            if channel.source is not None
            else await self.seats.direct_channel(pod_id=pod_id, user_id=recipient)
        )
        agent_id = channel.surface.agent_id
        await self.seats.lock_person(
            channel_id=channel_id,
            pod_id=pod_id,
            agent_id=agent_id,
            person_id=recipient,
        )
        found = await self.seats.conversation_in_channel(
            channel_id=channel_id,
            pod_id=pod_id,
            agent_id=agent_id,
            user_id=recipient,
            contact_id=None,
        )
        if found is not None:
            return found
        return await self.open_conversation(
            channel, notification=notification, channel_id=channel_id
        )

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
        await agent_conversations.append_notification_message(
            self.uow,
            conversation_id=conversation_id,
            message=message,
            notification_id=notification.id,
        )

    async def open_conversation(
        self,
        channel: DeliveryChannel,
        *,
        notification: NotificationEntity,
        channel_id: UUID,
    ) -> UUID | None:
        surface = channel.surface
        conversation = await agent_conversations.open_surface_conversation(
            self.uow,
            pod_id=notification.pod_id,
            agent_name=await self.egress.agent_name_for_surface(surface),
            user_id=notification.recipient_user_id,
            channel_id=channel_id,
            title=notification.title,
            metadata={
                "source": "notification",
                "notification_id": str(notification.id),
                "surface_id": str(surface.id),
                "surface_platform": surface.surface_type.value,
                "external_user_id": channel.external_user_id,
                "conversation_kind": (
                    "EMAIL" if surface.surface_type.is_email else "DM"
                ),
            },
            require_execute_grant=False,
        )
        return conversation.id


__all__ = ["NotificationEgress"]
