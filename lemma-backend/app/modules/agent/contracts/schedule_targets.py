"""An agent, as the thing a schedule fires.

Two lookups, by id and by pod-scoped name, because `ScheduleService` does both:
one for a schedule that names its agent outright, one for a schedule authored
against a name. They replace the half of `app/composition/schedule_targets.py`
that reached for `AgentRepository` from outside this module.

Not `provisioning.get_agent`, which was the nearest existing operation and is
the wrong one twice over. It is name-only, so there was nothing to answer the
id lookup with; and it takes a `Context`, because it is the operation a *person*
provisioning a pod goes through. A schedule firing has no requester -- the
authorization that matters happened when the schedule was created and is
re-checked against the schedule's own owner -- so a lookup demanding a context
would have to be handed a fabricated one.

`instruction` is why this returns `ScheduleTarget` rather than a summary. A
schedule may fire an agent with no message of its own, in which case the agent's
standing instruction is the whole of what it is asked to do, and
`validate_target_instruction` refuses the schedule when neither side has one.
`PodAgentSummary` does not carry it, so a summary cannot answer that question.

A submodule rather than `contracts/__init__`, like its siblings here: this
reaches the repository layer, and `contracts/__init__` is imported by anything
that wants any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Agent
from app.modules.agent.infrastructure.repositories.agent_repository import (
    AgentRepository,
)
from app.modules.schedule.contracts.targets import ScheduleTarget


async def agent_schedule_target(
    uow: SqlAlchemyUnitOfWork, agent_id: UUID
) -> ScheduleTarget | None:
    """The agent with this id, or ``None`` when it no longer exists."""
    return _target(await AgentRepository(uow).get(agent_id))


async def agent_schedule_target_by_name(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, name: str
) -> ScheduleTarget | None:
    """The pod's agent of this name, or ``None`` when it has none."""
    return _target(
        await AgentRepository(uow).get_by_pod_and_name(pod_id=pod_id, name=name)
    )


def _target(agent: Agent | None) -> ScheduleTarget | None:
    if agent is None:
        return None
    return ScheduleTarget(
        id=agent.id,
        pod_id=agent.pod_id,
        name=agent.name,
        instruction=agent.instruction,
    )


__all__ = ["agent_schedule_target", "agent_schedule_target_by_name"]
