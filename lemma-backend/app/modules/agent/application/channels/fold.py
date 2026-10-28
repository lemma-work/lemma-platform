"""Conversations that follow their people when contacts or channels merge.

A person has one live conversation per agent in a channel. When two contacts
turn out to be one person, or a channel folds into another, two conversations
can come to claim one seat: the one already seated keeps it, the other leaves
the channel, and what the person saw of it is copied into the one that kept
the seat -- so the agent answering them next has it, and so does their chat
from the other browser. The copies are in no run and marked ``folded_from``,
so an export does not carry them twice and the agent's history reads them as
the other chat's (``domain/folded_turns``).
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.db.channel_conversations import (
    ChannelConversations,
)
from app.modules.agent.infrastructure.db.contact_history import ContactHistory
from app.modules.agent.infrastructure.db.messages import Messages


class FoldConversations:
    """Moves conversations onto a surviving contact or channel."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.seats = ChannelConversations(uow)
        self.history = ContactHistory(uow)
        self.messages = Messages(uow)

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> FoldConversations:
        return cls(uow)

    async def merge_contact(
        self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> int:
        """The merged contact's conversations are the survivor's now.

        Where the survivor already has one with the same agent in the same
        channel, it keeps the seat. Returns how many changed hands.
        """
        pairs = await self.seats.unseated_by_merge(
            pod_id=pod_id, merged_id=merged_id, survivor_id=survivor_id
        )
        if pairs:
            await self.seats.unseat([orphan for orphan, _ in pairs])
            for orphan, seated in pairs:
                await self.fold(orphan, into=seated)
        return await self.seats.repoint_contact(
            pod_id=pod_id, merged_id=merged_id, survivor_id=survivor_id
        )

    async def move_channel(
        self, *, pod_id: UUID, from_channel_id: UUID, to_channel_id: UUID
    ) -> int:
        """A channel folded into another: its conversations work in that one
        now, but for those whose person is seated there already -- they stay
        behind, unseated when the emptied channel goes, folded into the seated
        one. Returns how many moved."""
        staying = await self.seats.staying_behind(
            pod_id=pod_id, from_channel_id=from_channel_id, to_channel_id=to_channel_id
        )
        for orphan, seated in staying:
            await self.fold(orphan, into=seated)
        return await self.seats.move_channel(
            pod_id=pod_id, from_channel_id=from_channel_id, to_channel_id=to_channel_id
        )

    async def fold(self, orphan_id: UUID, *, into: UUID) -> None:
        """What the person saw of ``orphan_id``, added to ``into``. Copied, not moved: the runs, waits and answers that point at the
        unseated one stay with it. Appended after what the seated one holds."""
        for draft in await self.history.copies_of(orphan_id):
            await self.messages.append_message(
                conversation_id=into, agent_run_id=None, draft=draft
            )
