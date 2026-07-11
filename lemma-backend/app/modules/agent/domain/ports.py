"""Ports for agent module boundaries."""

from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.agent.domain.events import AgentDomainEvent
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentRuntimeConfig,
    AgentRunFinishResult,
    AgentRunStatus,
    ConversationStatus,
    ConversationType,
    HarnessKind,
    HarnessOptions,
    JsonObject,
    JsonValue,
    MessageDraft,
)


class Harness(Protocol):
    """Runtime adapter that hides the underlying agent framework."""

    kind: HarnessKind

    async def run(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: AgentContext,
        options: HarnessOptions,
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        """Execute one agent run and yield normalized events."""
        ...


class AgentRepository(Protocol):
    async def get(self, agent_id: UUID, ctx: Context | None = None) -> Agent | None: ...

    async def get_by_pod_and_name(
        self, *, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> Agent | None: ...

    async def create(self, agent: Agent) -> Agent: ...

    async def update(self, agent: Agent) -> Agent: ...

    async def delete(self, agent_id: UUID) -> None: ...

    async def list_by_pod(
        self,
        *,
        pod_id: UUID,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[Agent], UUID | None]: ...

    async def list_visible_by_pod(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[Agent], UUID | None]: ...


class ConversationRepository(Protocol):
    async def create_conversation(self, conversation: Conversation) -> Conversation: ...

    async def create_conversation_once(
        self,
        conversation: Conversation,
    ) -> tuple[Conversation, bool]: ...

    async def update_conversation(self, conversation: Conversation) -> Conversation: ...

    async def get_conversation(self, conversation_id: UUID) -> Conversation | None: ...

    async def list_conversations(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        status: ConversationStatus | None = None,
        conversation_type: ConversationType | None = None,
        metadata_filters: JsonObject | None = None,
        cursor: UUID | None = None,
        limit: int = 20,
    ) -> tuple[list[Conversation], UUID | None]: ...

    async def create_agent_run(
        self,
        *,
        conversation_id: UUID,
        agent_id: UUID | None,
        agent_runtime: AgentRuntimeConfig,
        parent_run_id: UUID | None = None,
        metadata: JsonObject | None = None,
    ) -> AgentRun: ...

    async def get_agent_run(self, agent_run_id: UUID) -> AgentRun | None: ...

    async def get_active_agent_run_for_update(
        self,
        conversation_id: UUID,
    ) -> AgentRun | None: ...

    async def get_active_agent_run(
        self,
        conversation_id: UUID,
    ) -> AgentRun | None: ...

    async def append_message(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None,
        draft: MessageDraft,
    ) -> Message: ...

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[Message], int | None]: ...

    async def finish_agent_run(
        self,
        *,
        agent_run_id: UUID,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        error: str | None = None,
        output_data: JsonValue | None = None,
    ) -> AgentRunFinishResult | None: ...

    def collect_events(self, events: Sequence[AgentDomainEvent]) -> None: ...
