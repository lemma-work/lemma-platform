"""Ports for agent module boundaries."""

from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.agent.domain.events import AgentDomainEvent
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.run_projections import (
    ConversationOpeningTexts,
    StaleAgentRunRef,
    StrandedConversationRef,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentRunApprovalDecision,
    AgentRuntimeConfig,
    AgentRunFinishResult,
    AgentRunStatus,
    ConversationAgentSelection,
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

    # Not `async def`: that would declare a coroutine *returning* an iterator,
    # which is not what either harness is. Both are async generator functions and
    # every caller iterates the return value directly, un-awaited.
    def run(
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
    """The full conversation/run/message surface, including both query mixins.

    Declared here in one piece on purpose. The implementation is assembled from
    a class and two mixins, and every time the port has listed only the subset
    some caller happened to need, the next caller reached past it -- #445 had to
    delete `load_runtime_history_by_run_id` from this Protocol for exactly the
    mirror-image reason. A port that is a strict subset of its implementation is
    not a narrower contract, it is an unenforced one.
    """

    async def create_conversation(self, conversation: Conversation) -> Conversation: ...

    async def create_conversation_once(
        self,
        conversation: Conversation,
    ) -> tuple[Conversation, bool]: ...

    async def update_conversation(self, conversation: Conversation) -> Conversation: ...

    async def get_conversation(
        self,
        conversation_id: UUID,
        *,
        include_messages: bool = False,
        include_runs: bool = False,
    ) -> Conversation | None: ...

    async def get_conversation_metadata_key(
        self,
        conversation_id: UUID,
        key: str,
    ) -> JsonValue | None: ...

    async def set_conversation_metadata_key(
        self,
        conversation_id: UUID,
        key: str,
        value: JsonValue,
    ) -> None: ...

    async def lock_conversation(self, conversation_id: UUID) -> None: ...

    async def get_conversation_opening_texts(
        self, conversation_id: UUID
    ) -> ConversationOpeningTexts: ...

    async def find_existing_voice_transcript(
        self, conversation_id: UUID, paths: tuple[str, ...]
    ) -> str | None: ...

    async def set_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
    ) -> None: ...

    async def list_conversations(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        agent_selection: ConversationAgentSelection[UUID],
        status: ConversationStatus | None = None,
        conversation_type: ConversationType | None = None,
        metadata_filters: JsonObject | None = None,
        parent_id: UUID | None = None,
        archived: bool = False,
        cursor: UUID | None = None,
        limit: int = 20,
    ) -> tuple[list[Conversation], UUID | None]: ...

    async def list_children(
        self,
        *,
        parent_id: UUID,
        user_id: UUID,
        limit: int = 50,
        include_runs: bool = True,
    ) -> list[Conversation]: ...

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

    async def get_latest_agent_run_for_conversation(
        self,
        conversation_id: UUID,
    ) -> AgentRun | None: ...

    async def list_stale_active_runs(
        self,
        *,
        cutoff_seconds: int,
        limit: int = 200,
    ) -> list[StaleAgentRunRef]: ...

    async def list_runs_stuck_stopping(
        self,
        *,
        cutoff_seconds: int,
        limit: int = 200,
    ) -> list[StaleAgentRunRef]: ...

    async def list_conversations_stranded_by_a_finished_run(
        self,
        *,
        cutoff_seconds: int,
        limit: int = 200,
    ) -> list[StrandedConversationRef]: ...

    async def run_has_only_user_messages(self, agent_run_id: UUID) -> bool: ...

    async def count_queued_user_messages(self, agent_run_id: UUID) -> int: ...

    async def claim_queued_user_messages(self, agent_run_id: UUID) -> list[Message]: ...

    async def list_agent_runs_with_messages(
        self,
        conversation_id: UUID,
    ) -> list[AgentRun]: ...

    async def list_agent_runs_with_messages_by_run_id(
        self,
        agent_run_id: UUID,
    ) -> list[AgentRun]: ...

    async def load_runtime_history_digests_by_run_id(
        self,
        agent_run_id: UUID,
    ) -> list[AgentRun]: ...

    async def attach_runtime_history_messages(
        self,
        runs: list[AgentRun],
        *,
        full_run_ids: set[UUID],
    ) -> list[AgentRun]: ...

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

    async def record_approval_decision(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
        agent_run_id: UUID | None,
        tool_name: str | None,
        decision: AgentRunApprovalDecision,
        response: JsonObject | None,
        resolved_by_user_id: UUID,
    ) -> bool: ...

    async def claim_approval_execution(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> bool: ...

    async def get_approval_decision(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> tuple[AgentRunApprovalDecision, JsonObject] | None: ...

    async def list_resolved_approval_ids(
        self,
        *,
        conversation_id: UUID,
    ) -> set[str]: ...

    async def get_tool_call(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> Message | None: ...

    async def get_tool_return(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> Message | None: ...

    async def unresolved_pausing_call_ids(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        pausing_tool_names: Sequence[str],
    ) -> list[str]: ...

    def collect_events(self, events: Sequence[AgentDomainEvent]) -> None: ...
