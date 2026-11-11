"""Where conversations sit in channels, and which contact or member keeps them.

A conversation is one agent working for one person -- a member, or a contact
-- on one platform of a channel: their WhatsApp, their email, Slack, or Lemma
itself (no platform). There is one per agent, person and platform
(``uq_agent_conversations_channel_person``): however many threads, numbers or
days the person writes across on a platform, their messages there continue
it, and what they say on another is another conversation -- so an answer, and
what the agent reads, never crosses from one to the other. These are the reads and
writes of that placement, and of whom a conversation answers.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    delete,
    exists,
    func,
    select,
    text,
    update,
)
from sqlalchemy.orm import aliased

from app.core.authorization.delegation import effective_agent_id
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models.conversation import (
    IN_LEMMA,
    ConversationModel,
)


def _platform(conversation: type[ConversationModel]) -> ColumnElement[str]:
    """A conversation's platform as the unique index reads it."""
    return func.coalesce(conversation.platform, IN_LEMMA)


def in_channel_query(
    *,
    channel_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    person_id: UUID,
    platform: str | None,
) -> Select[tuple[UUID]]:
    """Spelled as ``uq_agent_conversations_channel_person`` spells its columns
    and predicate, so that index answers it: ``coalesce`` for the platform,
    the agent and the person, and ``NOT is_archived`` -- ``is_archived IS
    false`` is not taken to imply the index's predicate. A contact's id never
    names a member, so the person's id alone tells the two apart. ``platform``
    None is the person's conversation kept in Lemma itself."""
    conversation = ConversationModel
    agent = effective_agent_id(agent_id, pod_id=pod_id)
    return (
        select(conversation.id)
        .where(
            conversation.channel_id == channel_id,
            _platform(conversation) == (platform or IN_LEMMA),
            conversation.pod_id == pod_id,
            func.coalesce(conversation.agent_id, conversation.pod_id) == agent,
            func.coalesce(conversation.contact_id, conversation.user_id) == person_id,
            conversation.parent_id.is_(None),
            ~conversation.is_archived,
        )
        .limit(1)
    )


def _live_in_a_channel(conversation: type[ConversationModel]) -> ColumnElement[bool]:
    """What ``uq_agent_conversations_channel_person`` holds to one per person."""
    return (
        conversation.channel_id.is_not(None)
        & conversation.parent_id.is_(None)
        & conversation.is_archived.is_(False)
    )


def _same_seat(
    one: type[ConversationModel], other: type[ConversationModel]
) -> ColumnElement[bool]:
    """The same agent on the same platform: the seat the unique index keeps."""
    return (
        func.coalesce(one.agent_id, one.pod_id)
        == func.coalesce(other.agent_id, other.pod_id)
    ) & (_platform(one) == _platform(other))


def _theirs(pod_id: UUID, contact_id: UUID) -> tuple[ColumnElement[bool], ...]:
    return (
        ConversationModel.pod_id == pod_id,
        ConversationModel.contact_id == contact_id,
    )


class ChannelConversations(UnitOfWorkRepository):
    """Conversations by the channel they work in and the person they answer."""

    async def in_channel(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        person_id: UUID,
        platform: str | None,
    ) -> UUID | None:
        return await self.session.scalar(
            in_channel_query(
                channel_id=channel_id,
                pod_id=pod_id,
                agent_id=agent_id,
                person_id=person_id,
                platform=platform,
            )
        )

    async def lock_person(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        person_id: UUID,
        platform: str | None,
    ) -> None:
        """Serialise finding-or-opening one person's conversation on a
        platform until this transaction ends."""
        agent = effective_agent_id(agent_id, pod_id=pod_id)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {
                "key": (
                    f"channel-person:{channel_id}:{platform or IN_LEMMA}:"
                    f"{agent}:{person_id}"
                )
            },
        )

    async def place(
        self, *, conversation_id: UUID, channel_id: UUID, platform: str | None
    ) -> None:
        """Put a conversation that has no channel yet on this platform of one,
        unless another already holds that seat -- one a contact merge unseated
        stays out beside the survivor's, the unique index or not. Under the
        seat's lock, as ``find_or_open`` takes it, so an open of that seat
        still uncommitted is waited for, not missed; and only while the
        conversation is still that person's -- one a contact merge moved to
        another between the read and the lock is left out, not placed under
        the wrong person's lock."""
        conversation, holder = ConversationModel, aliased(ConversationModel)
        seat = (
            await self.session.execute(
                select(
                    conversation.pod_id,
                    conversation.agent_id,
                    func.coalesce(conversation.contact_id, conversation.user_id),
                ).where(conversation.id == conversation_id)
            )
        ).first()
        if seat is None:
            return
        await self.lock_person(
            channel_id=channel_id,
            pod_id=seat[0],
            agent_id=seat[1],
            person_id=seat[2],
            platform=platform,
        )
        seat_taken = exists().where(
            _live_in_a_channel(holder),
            holder.channel_id == channel_id,
            _platform(holder) == (platform or IN_LEMMA),
            func.coalesce(holder.agent_id, holder.pod_id)
            == func.coalesce(conversation.agent_id, conversation.pod_id),
            func.coalesce(holder.contact_id, holder.user_id)
            == func.coalesce(conversation.contact_id, conversation.user_id),
        )
        await self.session.execute(
            update(conversation)
            .where(
                conversation.id == conversation_id,
                conversation.channel_id.is_(None),
                func.coalesce(conversation.contact_id, conversation.user_id) == seat[2],
                ~seat_taken,
            )
            .values(channel_id=channel_id, platform=platform)
        )

    async def channel_of(self, conversation_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(ConversationModel.channel_id).where(
                ConversationModel.id == conversation_id
            )
        )

    async def name_contact(self, *, conversation_id: UUID, contact_id: UUID) -> bool:
        """Make a conversation that answers nobody yet this contact's."""
        result = await self.session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.id == conversation_id,
                ConversationModel.contact_id.is_(None),
            )
            .values(contact_id=contact_id)
        )
        return bool(result.rowcount)

    async def give_to_member(
        self, *, conversation_id: UUID, contact_id: UUID, to_user_id: UUID
    ) -> bool:
        """Hand this contact's conversation to another member; only one marked
        as this contact's moves."""
        result = await self.session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.id == conversation_id,
                ConversationModel.contact_id == contact_id,
            )
            .values(user_id=to_user_id)
        )
        return bool(result.rowcount)

    async def forget_contact(self, *, pod_id: UUID, contact_id: UUID) -> int:
        result = await self.session.execute(
            delete(ConversationModel).where(*_theirs(pod_id, contact_id))
        )
        return int(result.rowcount or 0)

    async def hold_contacts(self, *, pod_id: UUID, contact_id: UUID) -> None:
        """Row-lock a contact's conversations until this transaction ends: a
        placement of one waits for the merge, then finds it the survivor's
        and stays out; one already placing is waited for, and seen seated."""
        await self.session.execute(
            select(ConversationModel.id)
            .where(*_theirs(pod_id, contact_id))
            .with_for_update()
        )

    async def unseated_by_merge(
        self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> list[tuple[UUID, UUID]]:
        """``(merged contact's conversation, survivor's)`` pairs that share an
        agent and a channel: the survivor's keeps the seat."""
        conversation, theirs = ConversationModel, aliased(ConversationModel)
        rows = await self.session.execute(
            select(conversation.id, theirs.id)
            .join(
                theirs,
                and_(
                    _live_in_a_channel(theirs),
                    theirs.channel_id == conversation.channel_id,
                    _same_seat(theirs, conversation),
                    theirs.contact_id == survivor_id,
                ),
            )
            .where(*_theirs(pod_id, merged_id), _live_in_a_channel(conversation))
        )
        return [(row[0], row[1]) for row in rows]

    async def unseat(self, conversation_ids: list[UUID]) -> None:
        await self.session.execute(
            update(ConversationModel)
            .where(ConversationModel.id.in_(conversation_ids))
            .values(channel_id=None)
            .execution_options(synchronize_session=False)
        )

    async def repoint_contact(
        self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> int:
        result = await self.session.execute(
            update(ConversationModel)
            .where(*_theirs(pod_id, merged_id))
            .values(contact_id=survivor_id)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    def _same_person_in(self, theirs, conversation, channel_id: UUID):
        return and_(
            _live_in_a_channel(theirs),
            theirs.channel_id == channel_id,
            _same_seat(theirs, conversation),
            func.coalesce(theirs.contact_id, theirs.user_id)
            == func.coalesce(conversation.contact_id, conversation.user_id),
        )

    async def staying_behind(
        self, *, pod_id: UUID, from_channel_id: UUID, to_channel_id: UUID
    ) -> list[tuple[UUID, UUID]]:
        """``(conversation here, the same person's there)`` for each person who
        already has a live conversation with the same agent in ``to_channel_id``."""
        conversation, theirs = ConversationModel, aliased(ConversationModel)
        rows = await self.session.execute(
            select(conversation.id, theirs.id)
            .join(theirs, self._same_person_in(theirs, conversation, to_channel_id))
            .where(
                conversation.pod_id == pod_id,
                conversation.channel_id == from_channel_id,
                _live_in_a_channel(conversation),
            )
        )
        return [(row[0], row[1]) for row in rows]

    async def move_channel(
        self, *, pod_id: UUID, from_channel_id: UUID, to_channel_id: UUID
    ) -> int:
        """Every conversation in ``from_channel_id`` whose person has no live
        one with the same agent in ``to_channel_id`` moves there."""
        conversation, theirs = ConversationModel, aliased(ConversationModel)
        seated_there = (
            select(theirs.id)
            .where(self._same_person_in(theirs, conversation, to_channel_id))
            .exists()
        )
        result = await self.session.execute(
            update(conversation)
            .where(
                conversation.pod_id == pod_id,
                conversation.channel_id == from_channel_id,
                ~(_live_in_a_channel(conversation) & seated_there),
            )
            .values(channel_id=to_channel_id)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)
