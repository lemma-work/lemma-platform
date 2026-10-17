"""Writing to a channel's timeline, and reading it as somebody may.

Posting makes the person who spoke a participant -- a member or a contact,
never an agent, which is attached instead -- and announces the line
(``ChannelMessagePosted``) in the same transaction, for whatever has to follow
it: delivering a member's post, waking somebody's agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.channels.contracts.facts import ChannelMessagePosted
from app.modules.channels.domain.entities import (
    Channel,
    ChannelMessage,
    Principal,
    Sender,
    SenderKind,
)
from app.modules.channels.domain.projection import Reader, readable
from app.modules.channels.infrastructure.repository import ChannelRepository


#: The longest line a timeline keeps. A message is what people saw, so this is
#: generous -- a pasted document is cut, not refused.
MAX_TEXT_CHARS = 20_000


class ChannelNotFound(LookupError):
    """The channel is gone, or was never this pod's."""


class Timeline:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.channels = ChannelRepository(uow.session)

    async def post(
        self,
        channel: Channel,
        *,
        sender: Sender,
        text: str,
        source_id: UUID | None = None,
        members_only: bool = False,
        conversation_id: UUID | None = None,
        external_message_id: str | None = None,
        external_thread_id: str | None = None,
        reply_ref: Mapping[str, object] | None = None,
    ) -> ChannelMessage | None:
        """The line, posted; ``None`` when there was nothing to say.

        A platform message heard before is the line it already made, and is
        not announced again.
        """
        body = text.strip()[:MAX_TEXT_CHARS]
        if not body:
            return None
        message, written = await self.channels.append(
            channel=channel,
            sender=sender,
            text=body,
            source_id=source_id,
            members_only=members_only,
            conversation_id=conversation_id,
            external_message_id=external_message_id,
            external_thread_id=external_thread_id,
            reply_ref=reply_ref,
        )
        if not written:
            return message
        speaker = _principal_of(sender)
        if speaker is not None:
            await self.channels.join(channel=channel, principal=speaker)
        elif sender.agent_id is not None:
            await self.channels.attach_agent(
                channel_id=channel.id, agent_id=sender.agent_id
            )
        await facts.emit(
            self.uow,
            ChannelMessagePosted(
                pod_id=channel.pod_id,
                channel_id=channel.id,
                message_id=message.id,
                sender_kind=sender.kind,
                source_id=source_id,
                members_only=members_only,
            ),
        )
        return message

    async def background(
        self, channel_id: UUID, *, reader: Reader, limit: int
    ) -> list[ChannelMessage]:
        """The recent lines ``reader`` may see, oldest first.

        A contact reads only their source, so only their source is read; a
        member reads the whole channel.
        """
        if not reader.is_member and reader.source_id is None:
            return []
        recent = await self.channels.recent(
            channel_id,
            limit=limit,
            source_id=None if reader.is_member else reader.source_id,
        )
        return readable(reader, recent)

    async def channel(self, channel_id: UUID, *, pod_id: UUID) -> Channel:
        found = await self.channels.get(channel_id)
        if found is None or found.pod_id != pod_id:
            raise ChannelNotFound(str(channel_id))
        return found


def _principal_of(sender: Sender) -> Principal | None:
    if sender.kind is SenderKind.MEMBER and sender.user_id is not None:
        return Principal.member(sender.user_id)
    if sender.kind is SenderKind.CONTACT and sender.contact_id is not None:
        return Principal.contact(sender.contact_id)
    return None
