"""``schedule.agent_finished``: a schedule-started conversation's outcome, on its run.

Subscribed to the agent module's ``AgentRunFinished``, written in the
transaction that finished the run. Replaces the ``schedule-agent-outcomes``
consumer group, which re-read the conversation to learn what the event did not
say: the fact carries the conversation's status as of the same commit.

A conversation outlives its runs. A run that ends on a pause (``WAITING``) is
not the conversation finishing, so nothing is recorded; the run that ends the
pause emits its own fact. FAST: the ledger update and the schedule's failure
streak commit with the item's deletion (``outcome.run_target_outcome``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.schedule.work import outcome
from app.modules.schedule.work.kinds import TargetOutcome

SCHEDULE_AGENT_FINISHED = "schedule.agent_finished"

#: A conversation in one of these has finished, as far as its schedule is told.
_TERMINAL = frozenset({"COMPLETED", "FAILED", "STOPPED"})


def started_by_schedule(fact: AgentRunFinished) -> bool:
    return fact.origin == "schedule" and fact.conversation_status in _TERMINAL


async def record_agent_outcome(ctx: FastContext, payload: AgentRunFinished) -> None:
    status = payload.conversation_status
    await outcome.run_target_outcome(
        ctx,
        TargetOutcome(
            target_kind="AGENT",
            target_run_id=str(payload.conversation_id),
            status=status,
            completed_at=datetime.now(timezone.utc),
            error_type="AgentConversationFailed" if status == "FAILED" else None,
        ),
    )


def register_agent_outcome_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SCHEDULE_AGENT_FINISHED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=AgentRunFinished,
    )(record_agent_outcome)


register_agent_outcome_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=SCHEDULE_AGENT_FINISHED,
    cls=WorkClass.EVENTS,
    when=started_by_schedule,
    # A conversation's later run reports again under its own id.
    dedup=lambda fact: f"schedule-outcome:{fact.run_id}:{fact.conversation_status}",
)


__all__ = ["SCHEDULE_AGENT_FINISHED", "record_agent_outcome", "started_by_schedule"]
