"""A conversation that lost its seat in a merge, read on in the one that kept it.

Two contacts that turn out to be one person each had a conversation with the
same agent in their channel; one keeps the seat (``contact_conversations``),
and what the person saw of the other is copied into it here, so the agent
answering them next has it, and so does the other browser's chat. The copies
are in no run, so the agent reads them only because its history carries them
on purpose, as another chat's (``domain/folded_turns``).
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.contact_conversations import (
    MAX_EXPORTED_MESSAGES,
    _seen_by_them,
    _was_seen,
)
from app.modules.agent.domain.folded_turns import FOLDED_FROM_KEY
from app.modules.agent.domain.value_objects import (
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models.conversation import MessageModel
from app.modules.agent.infrastructure.db.messages import Messages


async def fold_history(
    uow: SqlAlchemyUnitOfWork, orphan_id: UUID, *, into: UUID
) -> None:
    """What the person saw of a conversation that lost its seat in a merge,
    added to the one that kept it: the agent answering them next has it, and
    so does their chat from the other browser, read back from that one.

    Copied, not moved -- the runs, waits and answers that point at the
    unseated one stay with it -- and marked (``FOLDED_FROM_KEY``), so an
    export does not carry it twice and the agent's history reads it as the
    other chat's. Appended after what the seated one holds.
    """
    rows = await uow.session.execute(
        _seen_by_them()
        .where(MessageModel.conversation_id == orphan_id)
        .order_by(MessageModel.sequence)
        .limit(MAX_EXPORTED_MESSAGES)
    )
    seen = [
        message
        for message, run_metadata in rows
        if _was_seen(message, run_metadata)
        and FOLDED_FROM_KEY not in (message.message_metadata or {})
    ]
    messages = Messages(uow)
    for message in seen:
        await messages.append_message(
            conversation_id=into,
            agent_run_id=None,
            draft=MessageDraft(
                role=MessageRole(message.role),
                kind=MessageKind(message.kind),
                text=message.text,
                metadata={
                    **(message.message_metadata or {}),
                    FOLDED_FROM_KEY: str(orphan_id),
                },
            ),
        )


__all__ = ["fold_history"]
