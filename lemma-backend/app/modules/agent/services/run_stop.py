"""Stopping a run, by whether a worker holds its turn.

A turn is a work item. Still queued, nobody is executing it: the stop deletes
the item and ends the run STOPPED in the same transaction, with the follow-ups
any finished run writes -- no worker, no harness call, no sweep to settle it
later. Held by a worker, the run is marked STOP_REQUESTED and the worker ends it
at its next check. A run with no item at all (one executed inline by its
caller, or by the previous runtime during a rollout) is ended STOPPED here too:
otherwise nothing would ever move it, now that the stuck-stop sweep is gone.

Replaces the ``AgentRunStopRequestedEvent`` -> consumer -> streaq status probe
round trip, and ``stuck_stops.settle_stuck_stops`` behind it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, pod_namespace
from app.modules.agent.domain.value_objects import (
    AgentRunFinishResult,
    AgentRunStatus,
    JsonObject,
)
from app.modules.agent.events.work_kinds import AGENT_TURN_KINDS, turn_key
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.realtime import (
    completed_payload,
    publish_conversation_event,
)
from app.modules.agent.services.run_followups import stage_run_followups


def _registered() -> None:
    import app.modules.agent.events.turn_work  # noqa: F401


async def turn_is_running(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, run_id: UUID
) -> bool:
    """Drop the run's queued turn; True when a worker is executing it instead."""
    _registered()
    work = WorkWriter.of(uow)
    running = False
    for kind in AGENT_TURN_KINDS:
        outcome = await work.cancel(kind, pod_namespace(pod_id), turn_key(run_id))
        running = running or outcome == "running"
    return running


async def stop_run(
    uow: SqlAlchemyUnitOfWork,
    repository: Runs,
    *,
    pod_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    stamp: JsonObject | None = None,
) -> AgentRunFinishResult | None:
    """Stop ``run_id`` in ``uow``'s transaction; see the module docstring."""
    if await turn_is_running(uow, pod_id=pod_id, run_id=run_id):
        return await repository.finish_agent_run(
            agent_run_id=run_id, status=AgentRunStatus.STOP_REQUESTED, stamp=stamp
        )
    finish = await repository.finish_agent_run(
        agent_run_id=run_id, status=AgentRunStatus.STOPPED, stamp=stamp
    )
    if finish is None or not finish.updated:
        return finish
    await stage_run_followups(
        uow, run_id=run_id, conversation_id=conversation_id, finish=finish
    )

    async def _announce() -> None:
        await publish_conversation_event(
            conversation_id,
            completed_payload(
                conversation_id=conversation_id,
                agent_run_id=run_id,
                status=finish.status.value,
                data={"conversation_status": finish.conversation_status.value},
            ),
        )

    uow.after_commit(_announce)
    return finish


__all__ = ["stop_run", "turn_is_running"]
