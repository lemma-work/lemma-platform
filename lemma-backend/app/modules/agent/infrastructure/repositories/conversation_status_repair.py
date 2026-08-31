"""Finding conversations an already-finished run left behind, and settling them.

Split out of the repository for the reason ``conversation_run_queries`` was:
that module is at the architecture ratchet's size limit. It is one function,
and it is the only write in the codebase that exists to correct a state rather
than to record one, so it reads better named than buried anyway.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, true

from app.core.log.log import get_logger
from app.modules.agent.domain.conversation_lifecycle import (
    ACTIVE_CONVERSATION_STATUSES,
    TERMINAL_CONVERSATION_STATUSES,
)
from app.modules.agent.domain.value_objects import ConversationStatus
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.infrastructure.repository_status import (
    conversation_status_values_for_db,
    run_status_values_for_db,
)
from app.modules.agent.infrastructure.run_projections import StrandedConversationRef
from app.modules.agent.domain.value_objects import TERMINAL_AGENT_RUN_STATUSES

logger = get_logger(__name__)

_ACTIVE_CONVERSATION_STATUS_VALUES = conversation_status_values_for_db(
    ACTIVE_CONVERSATION_STATUSES
)
_TERMINAL_AGENT_RUN_STATUS_VALUES = run_status_values_for_db(
    TERMINAL_AGENT_RUN_STATUSES
)


async def list_conversations_stranded_by_a_finished_run(
    session,
    *,
    cutoff_seconds: int,
    limit: int = 200,
) -> list[StrandedConversationRef]:
    """Conversations still active whose most recent run already finished.

    The blind spot in ``list_stale_active_runs``, which asks only about runs:
    where a run reached a terminal status but its conversation did not, the run
    is no longer active, so no sweep keyed on run status can see it — and
    nothing else will ever move it, because a terminal run is never finalized
    again. `schedule_run_recovery` and `workflow_agent` both read
    ``conversation.status``, so one of these wedges whatever waits on it.

    The cutoff is applied to the run's ``finished_at``, so a conversation is
    only picked up once its run has been done long enough that no in-flight
    finalization could still be on its way.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)
    # LATERAL rather than a DISTINCT ON subquery, and the difference is the
    # whole cost of this cron: DISTINCT ON has nothing to filter on before it
    # runs, so Postgres sorts every row in `agent_runs` to group them, every ten
    # minutes, growing with the table. This drives from the active
    # conversations, which the status index makes a small set.
    newest_run = (
        select(
            AgentRunModel.status.label("status"),
            AgentRunModel.finished_at.label("finished_at"),
        )
        .where(AgentRunModel.conversation_id == ConversationModel.id)
        .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
        .limit(1)
        .lateral("newest_run")
    )
    result = await session.execute(
        select(ConversationModel.id, newest_run.c.status)
        .select_from(ConversationModel)
        .join(newest_run, true())
        .where(
            ConversationModel.status.in_(_ACTIVE_CONVERSATION_STATUS_VALUES),
            newest_run.c.status.in_(_TERMINAL_AGENT_RUN_STATUS_VALUES),
            newest_run.c.finished_at.is_not(None),
            newest_run.c.finished_at < cutoff,
        )
        .order_by(ConversationModel.id)
        .limit(limit)
    )
    return [StrandedConversationRef(*row) for row in result.all()]


async def reconcile_conversation_to_terminal(
    session,
    *,
    conversation_id: UUID,
    status: ConversationStatus,
) -> bool:
    """Settle a conversation its finished run left behind. True if it moved.

    Only moves one that is still *active*. ``WAITING`` is left alone on purpose:
    a run that ended by asking a question leaves its conversation waiting, and
    that is the correct resting state, not a stuck one — collapsing it would
    tear down the pause ``request_approval`` and ``ask_user`` are built on. One
    that is already terminal is left alone too, so an ordinary second finalize
    writes nothing and reports nothing.
    """
    if status not in TERMINAL_CONVERSATION_STATUSES:
        return False
    conversation = await session.get(ConversationModel, conversation_id)
    if conversation is None:
        return False
    if ConversationStatus(conversation.status) not in ACTIVE_CONVERSATION_STATUSES:
        return False
    conversation.status = status.value
    await session.flush()
    logger.warning(
        "agent.conversation_repository.conversation_status_reconciled.degraded",
        conversation_id=str(conversation_id),
    )
    return True


async def settle_stranded_conversations(repository, *, cutoff_seconds: int) -> int:
    """Settle every conversation an already-finished run left behind.

    Returns how many moved, so a caller can decide whether it is worth saying.
    """
    stranded = await repository.list_conversations_stranded_by_a_finished_run(
        cutoff_seconds=cutoff_seconds
    )
    for conversation in stranded:
        await repository.set_conversation_status(
            conversation_id=conversation.id,
            status=ConversationStatus(conversation.run_status),
        )
    if stranded:
        logger.warning(
            "agent.conversation_status_repair.stranded_conversations_settled.degraded",
            count=len(stranded),
        )
    return len(stranded)


async def settle_stuck_stops(repository, *, cutoff_seconds: int):
    """Finish stops that no worker ever acted on, as STOPPED.

    A live worker acts on a stop within a second, so one still pending after the
    cutoff means none will. STOP_REQUESTED is an active status and holds the
    conversation's one active run slot, so until it settles a new message
    attaches to the dying run and starts nothing, and Retry refuses. Before this
    the only thing that freed the conversation was the orphan sweep, an hour
    after the run started.

    STOPPED rather than FAILED: the user asked for this one to end, and it did.
    """
    from app.modules.agent.domain.events import AgentRunCompletedEvent
    from app.modules.agent.domain.value_objects import AgentRunStatus

    settled = []
    for run in await repository.list_runs_stuck_stopping(cutoff_seconds=cutoff_seconds):
        result = await repository.finish_agent_run(
            agent_run_id=run.id,
            status=AgentRunStatus.STOPPED,
        )
        if result is None or not result.updated:
            continue
        repository.collect_events(
            [
                AgentRunCompletedEvent(
                    conversation_id=run.conversation_id,
                    agent_run_id=run.id,
                    status=result.status,
                    data={},
                )
            ]
        )
        settled.append((run.conversation_id, run.id, result.status))
    return settled
