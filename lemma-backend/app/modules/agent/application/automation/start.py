"""Starting an agent run that automation asked for: a workflow step or a schedule.

Nobody is in the chat. The conversation is opened with its typed origin
(``WORKFLOW_STEP`` or ``SCHEDULE``), the run with its trigger, and the first
message is the step's input or the firing's account of itself
(``domain/automation_prompts``) -- then the turn is staged in the same
transaction. A conversation id reserved by the caller makes a retried start
the same start.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.services.turn_staging import AgentTurnStart, stage_agent_turn
from app.modules.agent.domain.automation_prompts import (
    conversation_title,
    schedule_firing,
    schedule_wake_prompt,
    workflow_input_prompt,
)
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.project_repo import parse_project_repo
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.value_objects import (
    ConversationOrigin,
    ConversationType,
    MessageDraft,
    MessageRole,
    RunTrigger,
)
from app.modules.agent.infrastructure.conversation_idempotency_store import (
    create_conversation_for_id,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.runtime_resolution import (
    default_agent_runtime_for_pod,
)
from app.modules.pod.contracts.agent_access import pod_organization_id

#: The trigger each automated origin starts its run with, and the ``source``
#: its first message has always been labelled with.
_TRIGGERS: dict[ConversationOrigin, tuple[RunTrigger, str]] = {
    ConversationOrigin.SCHEDULE: (RunTrigger.SCHEDULE_FIRE, "SCHEDULE"),
    ConversationOrigin.WORKFLOW_STEP: (RunTrigger.WORKFLOW_START, "WORKFLOW_RUN"),
}

#: The pod's default runtime (``services/runtime_resolution``).
DefaultRuntime = Callable[..., Awaitable[AgentRuntimeConfig]]


class StartAutomatedRun:
    """Opens a workflow step's or a schedule's conversation and starts its run."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        default_runtime: DefaultRuntime = default_agent_runtime_for_pod,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.messages = Messages(uow)
        self._default_runtime = default_runtime

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> StartAutomatedRun:
        return cls(uow)

    async def default_runtime(self, pod_id: UUID) -> AgentRuntimeConfig:
        """This module's default runtime for the pod -- the same one a run
        started from a conversation resolves."""
        return await self._default_runtime(self.uow, pod_id=pod_id)

    async def organization_of(self, pod_id: UUID) -> UUID | None:
        return await pod_organization_id(self.uow, pod_id)

    async def start(
        self,
        *,
        agent: Agent,
        origin: ConversationOrigin,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        conversation_metadata: dict[str, object] | None = None,
        instructions: str | None = None,
        runtime_override: AgentRuntimeConfig | None = None,
    ) -> UUID:
        """The conversation's id. ``conversation_metadata`` is the caller's
        account of where it came from: a webhook's ``repo`` becomes the run's
        checkout, a schedule's ``schedule_type`` the conversation's, the rest
        stays as tags. ``instructions`` is what the run is for, layered after
        the agent's own; ``runtime_override`` a step's narrower model choice,
        kept for every later run of the conversation."""
        trigger, source = _TRIGGERS[origin]
        scheduled = origin is ConversationOrigin.SCHEDULE
        metadata = dict(conversation_metadata or {})
        repo = parse_project_repo(metadata.pop("repo", None))
        raw_type = metadata.pop("schedule_type", None)
        schedule_type = raw_type.upper() if isinstance(raw_type, str) else None
        entity = Conversation(
            **({"id": conversation_id} if conversation_id is not None else {}),
            user_id=user_id,
            pod_id=pod_id,
            organization_id=await self.organization_of(pod_id),
            agent_id=agent.id,
            # A schedule's runs are told apart by which schedule started them,
            # not by which agent they woke.
            title=conversation_title(agent.name, input_data, scheduled),
            type=ConversationType.TASK,
            metadata=metadata,
            origin=origin,
            workflow_run_id=workflow_run_id,
            schedule_type=schedule_type,
            repo=repo,
            instructions=instructions,
            agent_runtime=runtime_override,
        )
        if conversation_id is not None:
            conversation, created = await create_conversation_for_id(
                self.uow.session, entity
            )
            if not created:
                return conversation.id
        else:
            conversation = await self.conversations.create_conversation(entity)

        run = await self.runs.create_agent_run(
            conversation_id=conversation.id,
            agent_id=agent.id,
            agent_runtime=runtime_override
            or agent.agent_runtime
            or await self.default_runtime(pod_id),
            state=RunState(
                trigger=trigger,
                schedule_firing=schedule_firing(input_data, metadata, schedule_type)
                if scheduled
                else None,
            ),
        )
        await self.messages.append_message(
            conversation_id=conversation.id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text(
                schedule_wake_prompt(input_data, instructions)
                if scheduled
                else workflow_input_prompt(input_data),
                role=MessageRole.USER,
                metadata={"source": source, "content_format": "json"},
            ),
        )
        await stage_agent_turn(
            self.uow,
            AgentTurnStart(
                conversation_id=conversation.id,
                run_id=run.id,
                pod_id=pod_id,
                user_id=user_id,
                agent_name=agent.name,
                origin="schedule" if scheduled else "workflow",
            ),
        )
        return conversation.id
