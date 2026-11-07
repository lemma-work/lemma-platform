"""Whether a run is its person's own, asked for in Lemma -- read off its rows.

``domain/started_in_lemma`` is the rule; this reads what it decides on: the
run's kind (``agent_runs.kind``) and, for a sub-agent, the kind of the run
that spawned it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.domain.run_kind import RunKind
from app.modules.agent.domain.started_in_lemma import started_in_lemma
from app.modules.agent.infrastructure.db.runs import Runs


async def run_started_in_lemma(
    uow: SqlAlchemyUnitOfWork,
    conversation: Conversation,
    user_id: UUID,
    *,
    run: AgentRun | None = None,
) -> bool:
    """For ``run``, or -- when the caller knows only the conversation, as a
    request on the run's token does -- for the conversation's newest run: a
    conversation runs one turn at a time, and that is the one asking."""
    runs = Runs(uow)
    kind = (
        run.state.kind if run is not None else await runs.latest_kind(conversation.id)
    )
    parent_kind = None
    if kind is RunKind.SUBAGENT and conversation.parent_run_id is not None:
        parent = await runs.get_agent_run(conversation.parent_run_id)
        parent_kind = parent.state.kind if parent is not None else None
    return started_in_lemma(conversation, user_id, kind=kind, parent_kind=parent_kind)


__all__ = ["run_started_in_lemma"]
