"""The slice of a conversation's runs a prompt can possibly carry.

``runtime_history`` caps every conversation at ``MAX_HISTORY_AGENT_RUNS`` runs
and then trims surface conversations further by age and message count. The cap
was applied with ``runs[-60:]`` -- after every run of the conversation had been
read, and after a ``GROUP BY`` over every message in all of them had produced a
digest for each. A four-hundred-turn conversation paid for four hundred runs on
every single turn to send sixty.

Asking for the newest sixty instead is nearly the same query, with two things
that have to come back alongside it.

The **total** is one. The notice telling the model that older exchanges exist is
built from how many runs were removed, and a windowed list cannot know what it
was cut from; without the count the notice silently stops being accurate the
moment the window does any work.

The **run being resumed** is the other, and it is the one that can fail rather
than mislead. The runner finds it in the list it loaded and raises
``ConversationNotFoundError`` when it is absent -- so windowing alone turns a
resumed older run in a long conversation from a missing notice into a failed
run. It is fetched by id, in or out of the window.
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from uuid import UUID

from app.modules.agent.infrastructure.models import AgentRunModel


def newest_runs(conversation_id: UUID, limit: int) -> Select:
    """The newest ``limit`` runs of a conversation.

    Ordered descending so the limit takes the newest, and reversed by the caller
    -- the trims downstream read a list as chronological and the elision notice
    is attached to its first element.
    """
    return (
        select(AgentRunModel)
        .where(AgentRunModel.conversation_id == conversation_id)
        .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
        .limit(limit)
    )


def run_count(conversation_id: UUID) -> Select:
    """How many runs the conversation has, without reading any of them."""
    return (
        select(func.count())
        .select_from(AgentRunModel)
        .where(AgentRunModel.conversation_id == conversation_id)
    )
