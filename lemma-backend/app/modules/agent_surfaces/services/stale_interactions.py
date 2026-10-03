"""A tap on a question that is no longer being asked.

Buttons stay on screen after they stop meaning anything. On WhatsApp they stay
forever -- there is no edit API to take them away -- so a person scrolling back
and tapping "Yes" on yesterday's question is ordinary, not an edge case.

What that tap used to do depended on how the question ended. Answered: the
resolve path adopted the stored decision and said "Done", as though the new tap
had been recorded. Superseded by a newer message: the call had no pause left,
the resolve raised, and the person was told "I couldn't complete that action",
which reads as a fault worth retrying. Both are the same truth -- that question
is closed -- and this says it.

The check is against what the conversation is still waiting on rather than
against the decision table, because "still waiting on this call" is the only
state in which a tap can do anything; every other state is stale, however it
came about.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)

STALE_INTERACTION_TEXT = (
    "That question is no longer open, so I didn't record that tap. If you "
    "still need something, just send me a message."
)


async def is_still_open(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, tool_call_id: str
) -> bool:
    """Is the conversation still paused on ``tool_call_id``?

    All three lookups, because each answers "the oldest unresolved pause of its
    kind", and a tapped approval behind an older unanswered question is still
    open even though it is not the oldest pause of all.
    """
    if not tool_call_id:
        return False
    for lookup in (
        agent_conversations.pending_interaction,
        agent_conversations.pending_question,
        agent_conversations.pending_approval,
    ):
        pending = await lookup(uow, conversation_id)
        if pending is not None and pending.tool_call_id == tool_call_id:
            return True
    return False


__all__ = ["STALE_INTERACTION_TEXT", "is_still_open"]
