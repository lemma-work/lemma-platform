"""Agent execution adapter for workflow nodes."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.domain.value_objects import (
    ConversationStatus,
    ConversationType,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
)
from app.modules.pod.domain.pod_entities import PodConfig
from app.modules.pod.infrastructure.models.pod_models import Pod
from app.modules.workflow.domain.ports import AgentPort


class AgentControlAdapter(AgentPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.agent_repo = AgentRepository(uow)
        self.conversation_repo = ConversationRepository(uow)

    async def run_agent(
        self,
        agent_name: str,
        input_data: dict[str, Any],
        pod_id: UUID,
        user_id: UUID,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: dict[str, Any] | None = None,
        origin_type: str | None = None,
        origin_id: UUID | None = None,
    ) -> UUID:
        agent = await self.agent_repo.get_by_pod_and_name(
            pod_id=pod_id,
            name=agent_name,
        )
        if agent is None:
            raise AgentNotFoundError("Workflow agent target was not found")

        metadata = {**(conversation_metadata or {}), "source": source}
        if workflow_run_id is not None:
            metadata["workflow_run_id"] = str(workflow_run_id)
        entity = Conversation(
            user_id=user_id,
            pod_id=pod_id,
            organization_id=await self._get_pod_organization_id(pod_id),
            agent_id=agent.id,
            title=f"Workflow run: {agent.name}",
            type=ConversationType.TASK,
            metadata=metadata,
            origin_type=origin_type,
            origin_id=origin_id,
        )
        if origin_type is not None or origin_id is not None:
            conversation, created = (
                await self.conversation_repo.create_conversation_once(entity)
            )
            if not created:
                return conversation.id
        else:
            conversation = await self.conversation_repo.create_conversation(entity)

        runtime = agent.agent_runtime or await self._default_agent_runtime_for_pod(
            pod_id=pod_id
        )
        run = await self.conversation_repo.create_agent_run(
            conversation_id=conversation.id,
            agent_id=agent.id,
            agent_runtime=runtime,
            metadata=metadata,
        )
        await self.conversation_repo.append_message(
            conversation_id=conversation.id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text(
                self._workflow_input_prompt(input_data),
                role=MessageRole.USER,
                metadata={
                    "author_user_id": str(user_id),
                    **metadata,
                    "content_format": "json",
                },
            ),
        )
        self.conversation_repo.collect_events(
            [
                AgentRunStartedEvent(
                    conversation_id=conversation.id,
                    agent_run_id=run.id,
                    user_id=user_id,
                    pod_id=pod_id,
                    agent_name=agent.name,
                )
            ]
        )
        return conversation.id

    async def run_agent_by_id(
        self,
        agent_id: UUID,
        input_data: dict[str, Any],
        pod_id: UUID,
        user_id: UUID,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: dict[str, Any] | None = None,
        origin_type: str | None = None,
        origin_id: UUID | None = None,
    ) -> UUID:
        agent = await self.agent_repo.get(agent_id)
        if agent is None or agent.pod_id != pod_id:
            raise AgentNotFoundError("Workflow agent target was not found")
        return await self.run_agent(
            agent_name=agent.name,
            input_data=input_data,
            pod_id=pod_id,
            user_id=user_id,
            workflow_run_id=workflow_run_id,
            source=source,
            conversation_metadata=conversation_metadata,
            origin_type=origin_type,
            origin_id=origin_id,
        )

    async def get_conversation_status(self, conversation_id: UUID) -> dict[str, Any]:
        conversation = await self.conversation_repo.get_conversation(conversation_id)
        if conversation is None or conversation.status is None:
            return {"status": "NOT_FOUND"}
        output = self._normalize_agent_output(conversation.output)
        if conversation.status is ConversationStatus.COMPLETED:
            return {"status": "COMPLETED", "output_data": output}
        if conversation.status is ConversationStatus.WAITING:
            return {"status": "WAITING", "output_data": output}
        if conversation.status in {
            ConversationStatus.FAILED,
            ConversationStatus.STOPPED,
        }:
            return {
                "status": "FAILED",
                "error": f"Agent conversation {conversation.status.value}",
                "output_data": output,
            }
        return {"status": "RUNNING"}

    async def _default_agent_runtime_for_pod(
        self, *, pod_id: UUID
    ) -> AgentRuntimeConfig:
        result = await self.uow.session.execute(
            select(Pod.config).where(Pod.id == pod_id)
        )
        runtime = PodConfig.from_raw(
            result.scalar_one_or_none() or {}
        ).resolved_default_runtime()
        return runtime or AgentRuntimeConfig(
            profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
        )

    async def _get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        result = await self.uow.session.execute(
            select(Pod.organization_id).where(Pod.id == pod_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _normalize_agent_output(output: Any) -> dict[str, Any]:
        if isinstance(output, dict):
            return output
        if output is None or output == "":
            return {}
        return {"answer": output}

    @staticmethod
    def _workflow_input_prompt(input_data: dict[str, Any]) -> str:
        payload = json.dumps(input_data, ensure_ascii=True, indent=2, default=str)
        return f"Workflow input JSON:\n{payload}"
