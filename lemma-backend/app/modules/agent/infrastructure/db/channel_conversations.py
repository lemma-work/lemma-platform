"""Where conversations sit in channels, and which contact or member keeps them.

A conversation is one agent working for one person -- a member, or a contact
-- and in a channel there is one per agent and person
(``uq_agent_conversations_channel_person``): however many threads, numbers or
days the person writes across, their messages there continue it. These are
the reads and writes of that placement, and of whom a conversation answers.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.orm import aliased

from app.core.authorization.delegation import effective_agent_id
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models.conversation import ConversationModel


def in_channel_query(
    *, channel_id: UUID, pod_id: UUID, agent_id: UUID | None, person_id: UUID
) -> Select[tuple[UUID]]:
    """Spelled as ``uq_agent_conversations_channel_person`` spells its columns
    and predicate, so that index answers it: ``coalesce`` for the agent and
    the person, and ``NOT is_archived`` -- ``is_archived IS false`` is not
    taken to imply the index's predicate. A contact's id never names a
    member, so the person's id alone tells the two apart."""
    conversation = ConversationModel
    agent = effective_agent_id(agent_id, pod_id=pod_id)
    return (
        select(conversation.id)
        .where(
            conversation.channel_id == channel_id,
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


def _same_agent(
    one: type[ConversationModel], other: type[ConversationModel]
) -> ColumnElement[bool]:
    return func.coalesce(one.agent_id, one.pod_id) == func.coalesce(
        other.agent_id, other.pod_id
    )


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
    ) -> UUID | None:
        return await self.session.scalar(
            in_channel_query(
                channel_id=channel_id,
                pod_id=pod_id,
                agent_id=agent_id,
                person_id=person_id,
            )
        )

    async def lock_person(
        self, *, channel_id: UUID, pod_id: UUID, agent_id: UUID | None, person_id: UUID
    ) -> None:
        """Serialise finding-or-opening one person's conversation in a channel
        until this transaction ends."""
        agent = effective_agent_id(agent_id, pod_id=pod_id)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"channel-person:{channel_id}:{agent}:{person_id}"},
        )

    async def place(self, *, conversation_id: UUID, channel_id: UUID) -> None:
        """Put a conversation that has no channel yet in this one."""
        await self.session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.id == conversation_id,
                ConversationModel.channel_id.is_(None),
            )
            .values(channel_id=channel_id)
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
                    _same_agent(theirs, conversation),
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
            _same_agent(theirs, conversation),
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
