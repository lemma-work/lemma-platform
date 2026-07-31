"""Bind schedule target lookup ports to agent and workflow repositories."""

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.schedule.domain.interfaces import ScheduleTarget
from app.modules.workflow.domain.start import EventWorkflowStartConfig, WorkflowStartType
from app.modules.workflow.domain.workflow import WorkflowMode
from app.modules.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository


class SqlAlchemyScheduleTargetResolver:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._agents = AgentRepository(uow)
        self._workflows = SqlAlchemyWorkflowRepository(uow)

    async def get_workflow(self, workflow_id: UUID) -> ScheduleTarget | None:
        return self._workflow_target(await self._workflows.get(workflow_id))

    async def get_workflow_by_name(
        self, pod_id: UUID, name: str
    ) -> ScheduleTarget | None:
        return self._workflow_target(await self._workflows.get_by_name(pod_id, name))

    async def get_agent(self, agent_id: UUID) -> ScheduleTarget | None:
        return self._agent_target(await self._agents.get(agent_id))

    async def get_agent_by_name(
        self, pod_id: UUID, name: str
    ) -> ScheduleTarget | None:
        return self._agent_target(
            await self._agents.get_by_pod_and_name(pod_id=pod_id, name=name)
        )

    @staticmethod
    def _agent_target(agent) -> ScheduleTarget | None:
        if agent is None:
            return None
        return ScheduleTarget(id=agent.id, pod_id=agent.pod_id, name=agent.name)

    @staticmethod
    def _workflow_target(workflow) -> ScheduleTarget | None:
        if workflow is None:
            return None
        trigger_id = None
        trigger_config = None
        if workflow.start is not None and workflow.start.type is WorkflowStartType.EVENT:
            config = workflow.start.config
            if isinstance(config, EventWorkflowStartConfig):
                trigger_id = config.connector_trigger_id
                trigger_config = dict(config.trigger_config or {})
        return ScheduleTarget(
            id=workflow.id,
            pod_id=workflow.pod_id,
            name=workflow.name,
            is_global_workflow=workflow.mode is WorkflowMode.GLOBAL,
            event_trigger_id=trigger_id,
            event_trigger_config=trigger_config,
        )
