"""Driving an agent conversation on a workflow's behalf.

Implements `workflow`'s own `AgentPort`. Here rather than in a third package
because every collaborator it uses -- the agent and conversation repositories,
the wait store, the runtime profile default -- belongs to this module, and a
build step that lives elsewhere has to know where inside `agent` each of them
happens to sit.

Values crossing the port are `object`, not `Any`: a workflow's node output is
JSON whose shape belongs to the workflow's author, and saying `Any` would let a
caller read a key off it without the type checker asking.
"""

from __future__ import annotations

import json
from uuid import UUID

from app.composition.agent_pod import create_agent_pod_repository
from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.domain.events import (
    AgentRunStartedEvent,
    AgentRunStopRequestedEvent,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    ConversationType,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.infrastructure.conversation_idempotency_store import (
    create_conversation_for_id,
)
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.pod_runtime_defaults import (
    default_agent_runtime_for_pod,
)
from app.modules.workflow.contracts import AgentPort


class AgentControlAdapter(AgentPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.agent_repo = AgentRepository(uow)
        self.conversation_repo = ConversationRepository(uow)
        self.wait_repo = AgentConversationWaitRepository(uow)

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
    ) -> UUID:
        agent = await self.agent_repo.get_by_pod_and_name(
            pod_id=pod_id,
            name=agent_name,
        )
        if agent is None:
            raise AgentNotFoundError("Workflow agent target was not found")
        return await self._start_conversation(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_runtime=agent.agent_runtime,
            input_data=input_data,
            pod_id=pod_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_run_id=workflow_run_id,
            source=source,
            conversation_metadata=conversation_metadata,
            instructions=instructions,
        )

    async def _start_conversation(
        self,
        *,
        agent_id: UUID | None,
        agent_name: str,
        agent_runtime: AgentRuntimeConfig | None,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None,
        workflow_run_id: UUID | None,
        source: str,
        conversation_metadata: dict[str, object] | None,
        instructions: str | None,
    ) -> UUID:
        metadata: dict[str, object] = {
            **(conversation_metadata or {}),
            "source": source,
        }
        if workflow_run_id is not None:
            metadata["workflow_run_id"] = str(workflow_run_id)
        entity_values: dict[str, object] = {
            "user_id": user_id,
            "pod_id": pod_id,
            "organization_id": await self._get_pod_organization_id(pod_id),
            "agent_id": agent_id,
            "title": f"Workflow run: {agent_name}",
            "type": ConversationType.TASK,
            "metadata": metadata,
            # What the trigger asked for, in the author's words. Lands in the
            # system prompt as `# Conversation Instructions`, after the agent's
            # own instruction — so a named agent keeps its identity and gains a
            # task, and the default assistant, whose instruction is empty, gets
            # the only thing telling it why it woke up.
            "instructions": instructions,
        }
        if conversation_id is not None:
            entity_values["id"] = conversation_id
        entity = Conversation(
            **entity_values,
        )
        if conversation_id is not None:
            conversation, created = await create_conversation_for_id(
                self.uow.session,
                entity,
            )
            if not created:
                return conversation.id
        else:
            conversation = await self.conversation_repo.create_conversation(entity)

        runtime = agent_runtime or await self._default_agent_runtime_for_pod(
            pod_id=pod_id
        )
        run = await self.conversation_repo.create_agent_run(
            conversation_id=conversation.id,
            agent_id=agent_id,
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
                    agent_name=agent_name,
                )
            ]
        )
        return conversation.id

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
        agent = await self.agent_repo.get(agent_id)
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
        conversation = await self.conversation_repo.get_conversation(conversation_id)
        if conversation is None or conversation.status is None:
            return {"status": "NOT_FOUND"}
        output = self._normalize_agent_output(conversation.output)
        if conversation.status is ConversationStatus.COMPLETED:
            return {"status": "COMPLETED", "output_data": output}
        if conversation.status is ConversationStatus.WAITING:
            # The expiry policy needs to know *why* a conversation is waiting. An
            # agent blocked on a person is the hang the ceiling exists to catch;
            # a snoozed agent will wake itself and is perfectly healthy, so
            # failing its run would be a silent wrong outcome rather than a
            # visible error. See `run_resume_service._expire_overdue_wait`.
            snooze = await self.wait_repo.find_active_for_conversation(conversation_id)
            return {
                "status": "WAITING",
                "wait_reason": "SNOOZE" if snooze else "HUMAN",
                "wakes_at": snooze.scheduled_at.isoformat() if snooze else None,
                "output_data": output,
            }
        if conversation.status in {
            ConversationStatus.FAILED,
            ConversationStatus.STOPPED,
        }:
            # Carry the run's own error, not just the status. Without it the
            # workflow records "Agent conversation FAILED" and the reason -- which
            # the agent already wrote to `last_run_error` -- is lost, so the run
            # says an agent failed and nothing about why.
            reason = (conversation.last_run_error or "").strip()
            return {
                "status": "FAILED",
                "error": (
                    f"Agent conversation {conversation.status.value}: {reason}"
                    if reason
                    else f"Agent conversation {conversation.status.value}"
                ),
                "output_data": output,
            }
        return {"status": "RUNNING"}

    async def stop_conversation(self, conversation_id: UUID, user_id: UUID) -> None:
        """Ask the conversation's active run to stop.

        Mirrors ConversationService.stop_conversation minus the access checks —
        the caller is the engine cancelling a run it already authorized, not a
        user reaching in. Nothing to stop is success, not an error: the agent
        may have finished between the cancel and this call.
        """
        active_run = await self.conversation_repo.get_active_agent_run_for_update(
            conversation_id
        )
        if active_run is None:
            return
        await self.conversation_repo.finish_agent_run(
            agent_run_id=active_run.id,
            status=AgentRunStatus.STOP_REQUESTED,
        )
        self.conversation_repo.collect_events(
            [
                AgentRunStopRequestedEvent(
                    conversation_id=conversation_id,
                    agent_run_id=active_run.id,
                    user_id=user_id,
                )
            ]
        )

    async def _default_agent_runtime_for_pod(
        self, *, pod_id: UUID
    ) -> AgentRuntimeConfig:
        # This module's own answer, not a second copy of it. Reading
        # `Pod.config` here directly is how a workflow-started run could
        # resolve a different default model than the same agent started from a
        # conversation.
        return await default_agent_runtime_for_pod(self.uow, pod_id=pod_id)

    async def _get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        return await create_agent_pod_repository(self.uow).get_organization_id(pod_id)

    @staticmethod
    def _normalize_agent_output(output: object) -> dict[str, object]:
        if isinstance(output, dict):
            return output
        if output is None or output == "":
            return {}
        return {"answer": output}

    @staticmethod
    def _workflow_input_prompt(input_data: dict[str, object]) -> str:
        payload = json.dumps(input_data, ensure_ascii=True, indent=2, default=str)
        return f"Workflow input JSON:\n{payload}"
