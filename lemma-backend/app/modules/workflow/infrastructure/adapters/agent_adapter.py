"""Agent adapter for the workflow module."""

import json
from typing import Dict, Any
from uuid import UUID

from sqlalchemy import select

from app.modules.workflow.domain.ports import AgentPort
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    ConversationStatus,
    ConversationType,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
)
from app.modules.pod.domain.pod_entities import PodConfig
from app.modules.pod.infrastructure.models.pod_models import Pod


class AgentControlAdapter(AgentPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.agent_repo = AgentRepository(uow)
        self.conversation_repo = ConversationRepository(uow)

    async def run_agent(
        self,
        agent_name: str,
        input_data: Dict[str, Any],
        pod_id: UUID,
        user_id: UUID,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: Dict[str, Any] | None = None,
    ) -> UUID:
        """Start a pod agent conversation execution and return the conversation id."""

        agent = await self.agent_repo.get_by_pod_and_name(
            pod_id=pod_id,
            name=agent_name,
        )
        if not agent:
            raise AgentNotFoundError(f"Agent '{agent_name}' not found in pod {pod_id}")

        organization_id = await self._get_pod_organization_id(pod_id)
        conversation_metadata = {
            **(conversation_metadata or {}),
            "source": source,
        }
        if workflow_run_id is not None:
            conversation_metadata["workflow_run_id"] = str(workflow_run_id)
        conversation = await self.conversation_repo.create_conversation(
            Conversation(
                user_id=user_id,
                pod_id=pod_id,
                organization_id=organization_id,
                agent_id=agent.id,
                title=f"Workflow run: {agent.name}",
                type=ConversationType.TASK,
                metadata=conversation_metadata,
            )
        )
        agent_runtime = agent.agent_runtime or await self._default_agent_runtime_for_pod(
            pod_id=pod_id,
        )
        agent_run = await self.conversation_repo.create_agent_run(
            conversation_id=conversation.id,
            agent_id=agent.id,
            agent_runtime=agent_runtime,
            metadata=conversation_metadata,
        )
        await self.conversation_repo.append_message(
            conversation_id=conversation.id,
            agent_run_id=agent_run.id,
            draft=MessageDraft.of_text(
                self._workflow_input_prompt(input_data),
                role=MessageRole.USER,
                metadata={
                    "author_user_id": str(user_id),
                    **conversation_metadata,
                    "content_format": "json",
                },
            ),
        )
        self.conversation_repo.collect_events(
            [
                AgentRunStartedEvent(
                    conversation_id=conversation.id,
                    agent_run_id=agent_run.id,
                    user_id=user_id,
                    pod_id=pod_id,
                    agent_name=agent.name,
                ),
            ]
        )
        return conversation.id

    async def _default_agent_runtime_for_pod(
        self,
        *,
        pod_id: UUID,
    ) -> AgentRuntimeConfig:
        result = await self.uow.session.execute(
            select(Pod.config).where(Pod.id == pod_id)
        )
        config = result.scalar_one_or_none() or {}
        runtime = PodConfig.from_raw(config).resolved_default_runtime()
        return runtime or AgentRuntimeConfig(
            profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
        )

    async def run_agent_by_id(
        self,
        agent_id: UUID,
        input_data: Dict[str, Any],
        pod_id: UUID,
        user_id: UUID,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: Dict[str, Any] | None = None,
    ) -> UUID:
        agent = await self.agent_repo.get(agent_id)
        if not agent or agent.pod_id != pod_id:
            raise AgentNotFoundError(f"Agent '{agent_id}' not found in pod {pod_id}")
        return await self.run_agent(
            agent_name=agent.name,
            input_data=input_data,
            pod_id=pod_id,
            user_id=user_id,
            workflow_run_id=workflow_run_id,
            source=source,
            conversation_metadata=conversation_metadata,
        )

    async def get_conversation_status(self, conversation_id: UUID) -> Dict[str, Any]:
        conversation = await self.conversation_repo.get_conversation(conversation_id)
        if conversation is None:
            return {"status": "NOT_FOUND"}
        status = conversation.status
        if status is None:
            return {"status": "NOT_FOUND"}
        output = self._normalize_agent_output(conversation.output)
        if status == ConversationStatus.COMPLETED:
            return {
                "status": "COMPLETED",
                "output_data": output,
            }
        if status == ConversationStatus.WAITING:
            return {
                "status": "WAITING",
                "output_data": output,
            }
        if status in {ConversationStatus.FAILED, ConversationStatus.STOPPED}:
            return {
                "status": "FAILED",
                "error": f"Agent conversation {status.value}",
                "output_data": output,
            }
        return {"status": "RUNNING"}

    @staticmethod
    def _normalize_agent_output(output: Any) -> Dict[str, Any]:
        """Always hand the workflow a dict.

        An agent with no ``output_schema`` completes with a bare string (its
        final assistant text), so wrap that as ``{"answer": text}`` — the
        workflow then resumes cleanly and downstream nodes read
        ``<node_id>.answer``. Without this the run would hang: the stepper's
        resume does ``dict(output)``, which raises on a non-dict.
        """
        if isinstance(output, dict):
            return output
        if output is None or output == "":
            return {}
        return {"answer": output}

    async def _get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        result = await self.uow.session.execute(
            select(Pod.organization_id).where(Pod.id == pod_id)
        )
        return result.scalar_one_or_none()

    def _workflow_input_prompt(self, input_data: Dict[str, Any]) -> str:
        payload = json.dumps(input_data, ensure_ascii=True, indent=2, default=str)
        return f"Workflow input JSON:\n{payload}"
