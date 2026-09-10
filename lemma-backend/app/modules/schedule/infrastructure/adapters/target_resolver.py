"""The four target lookups `ScheduleService` makes, bound to a transaction.

Schedule's own adapter now, not a composition one. It used to construct
`AgentRepository` and `SqlAlchemyWorkflowRepository` and map their entities onto
`ScheduleTarget` itself, which meant the composition root -- not either owning
module -- held the answer to "what part of an agent is a schedule target".

What is left here is the part that really is `schedule`'s: which of the four
questions to ask. Each provider answers its own, through
`agent/contracts/schedule_targets.py` and `workflow/contracts/schedule_targets.py`.

Imported lazily, and measured: both contracts reach their module's repository
layer, and this adapter is constructed per `ScheduleService`, which the API and
the worker both build on paths that do not otherwise load either repository.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.contracts.targets import ScheduleTarget


class SqlAlchemyScheduleTargetResolver:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def get_workflow(self, workflow_id: UUID) -> ScheduleTarget | None:
        from app.modules.workflow.contracts.schedule_targets import (
            workflow_schedule_target,
        )

        return await workflow_schedule_target(self._uow, workflow_id)

    async def get_workflow_by_name(
        self, pod_id: UUID, name: str
    ) -> ScheduleTarget | None:
        from app.modules.workflow.contracts.schedule_targets import (
            workflow_schedule_target_by_name,
        )

        return await workflow_schedule_target_by_name(
            self._uow, pod_id=pod_id, name=name
        )

    async def get_agent(self, agent_id: UUID) -> ScheduleTarget | None:
        from app.modules.agent.contracts.schedule_targets import agent_schedule_target

        return await agent_schedule_target(self._uow, agent_id)

    async def get_agent_by_name(self, pod_id: UUID, name: str) -> ScheduleTarget | None:
        from app.modules.agent.contracts.schedule_targets import (
            agent_schedule_target_by_name,
        )

        return await agent_schedule_target_by_name(self._uow, pod_id=pod_id, name=name)


__all__ = ["SqlAlchemyScheduleTargetResolver"]
