"""How workflow and schedule drive an agent: start a step or a scheduled run,
read what became of it, stop it.

`workflow` holds an `AgentPort` for the length of a run, so this publishes one
binding of it (`build_agent_control_adapter`) rather than free functions the
engine would reassemble. The binding resolves the agent the step names and
hands everything else to the use cases (`application/automation`): a
``source`` of ``SCHEDULE`` is a schedule's conversation and run, anything else
a workflow step's.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.generation import GenerationSettings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.domain.value_objects import ConversationOrigin
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.workflow.contracts import AgentPort

#: The run source a schedule starts with; every other source is a workflow's.
SCHEDULE_SOURCE = "SCHEDULE"


class _AgentDriver(AgentPort):
    """`AgentPort` over this module's automation use cases, in one transaction."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        from app.modules.agent.application.automation.outcome import (
            AutomatedRunOutcome,
        )
        from app.modules.agent.application.automation.start import StartAutomatedRun

        self.agents = AgentRepository(uow)
        self.starts = StartAutomatedRun.build(uow)
        self.outcomes = AutomatedRunOutcome.build(uow)

    async def run_agent(
        self,
        agent_name: str,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: dict[str, object] | None = None,
        instructions: str | None = None,
        model_name: str | None = None,
        generation: GenerationSettings | None = None,
    ) -> UUID:
        agent = await self.agents.get_by_pod_and_name(pod_id=pod_id, name=agent_name)
        if agent is None:
            raise AgentNotFoundError("Workflow agent target was not found")
        override = None
        if model_name is not None or generation is not None:
            base = agent.agent_runtime or await self.starts.default_runtime(pod_id)
            override = base.with_overrides(model_name=model_name, settings=generation)
        return await self.starts.start(
            agent=agent,
            origin=ConversationOrigin.SCHEDULE
            if source == SCHEDULE_SOURCE
            else ConversationOrigin.WORKFLOW_STEP,
            input_data=input_data,
            pod_id=pod_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_run_id=workflow_run_id,
            conversation_metadata=conversation_metadata,
            instructions=instructions,
            runtime_override=override,
        )

    async def run_agent_by_id(
        self,
        agent_id: UUID,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: dict[str, object] | None = None,
        instructions: str | None = None,
    ) -> UUID:
        agent = await self.agents.get(agent_id)
        if agent is None or agent.pod_id != pod_id:
            raise AgentNotFoundError("Workflow agent target was not found")
        return await self.run_agent(
            agent_name=agent.name,
            input_data=input_data,
            pod_id=pod_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_run_id=workflow_run_id,
            source=source,
            conversation_metadata=conversation_metadata,
            instructions=instructions,
        )

    async def get_conversation_status(self, conversation_id: UUID) -> dict[str, object]:
        return await self.outcomes.status(conversation_id)

    async def stop_conversation(self, conversation_id: UUID, user_id: UUID) -> None:
        del user_id  # the engine already authorized the cancel
        await self.outcomes.stop(conversation_id)


def build_agent_control_adapter(uow: SqlAlchemyUnitOfWork) -> AgentPort:
    """An agent driver bound to this transaction."""
    return _AgentDriver(uow)


__all__ = ["SCHEDULE_SOURCE", "build_agent_control_adapter"]
