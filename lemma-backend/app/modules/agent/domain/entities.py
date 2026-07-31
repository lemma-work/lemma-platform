"""Domain entities for the unified agent module."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import Field

from app.core.authorization.context import ResourceType
from app.core.domain.entity import CreatedEntity, Entity
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentRunStatus,
    AgentToolset,
    ConversationStatus,
    ConversationType,
    JsonObject,
    JsonValue,
    MessageDraft,
    MessageKind,
    MessageRole,
)

class Agent(Entity):
    """Reusable agent definition.

    Persisted agents are pod-owned. The service may also build a virtual pod
    assistant entity with the same pod_id and no persisted agent row.
    """

    resource_type: ClassVar[ResourceType] = ResourceType.AGENT

    pod_id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    visibility: str = "POD"
    instruction: str
    agent_runtime: AgentRuntimeConfig | None = None
    toolsets: list[AgentToolset] = Field(default_factory=list)
    input_schema: JsonObject | None = None
    output_schema: JsonObject | None = None
    metadata: JsonObject | None = None
    allowed_actions: list[str] = Field(default_factory=list)


class Message(CreatedEntity):
    """Append-only message record with durable ordering.

    The body is flat: ``kind`` selects which fields are populated — ``text`` for
    textual kinds (text/notification/thinking), or
    ``tool_name``/``tool_call_id`` + ``tool_args`` (tool_call) /
    ``tool_result`` (tool_return) for tool kinds. There is no nested ``content``.
    """

    conversation_id: UUID
    sequence: int
    agent_run_id: UUID | None = None
    role: str
    kind: MessageKind
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: JsonValue | None = None
    tool_result: JsonValue | None = None
    metadata: JsonObject | None = None

    @property
    def is_visible(self) -> bool:
        return True

    @classmethod
    def create(
        cls,
        *,
        conversation_id: UUID,
        sequence: int,
        agent_run_id: UUID | None,
        role: MessageRole | str,
        kind: MessageKind = MessageKind.TEXT,
        text: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        tool_args: JsonValue | None = None,
        tool_result: JsonValue | None = None,
        metadata: JsonObject | None = None,
    ) -> "Message":
        role_value = role.value if isinstance(role, MessageRole) else str(role)
        return cls(
            conversation_id=conversation_id,
            sequence=sequence,
            agent_run_id=agent_run_id,
            role=role_value,
            kind=kind,
            text=text,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_args=tool_args,
            tool_result=tool_result,
            metadata=metadata,
        )

    @classmethod
    def from_draft(
        cls,
        draft: MessageDraft,
        *,
        conversation_id: UUID,
        sequence: int,
        agent_run_id: UUID | None,
    ) -> "Message":
        return cls(
            conversation_id=conversation_id,
            sequence=sequence,
            agent_run_id=agent_run_id,
            role=draft.role.value,
            kind=draft.kind,
            text=draft.text,
            tool_name=draft.tool_name,
            tool_call_id=draft.tool_call_id,
            tool_args=draft.tool_args,
            tool_result=draft.tool_result,
            metadata=draft.metadata,
        )


class Conversation(Entity):
    """Primary storage aggregate for pod assistant and pod agent chats."""

    user_id: UUID
    pod_id: UUID
    organization_id: UUID | None = None
    agent_id: UUID | None = None
    title: str | None = None
    instructions: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    origin_type: str | None = None
    origin_id: UUID | None = None
    parent_id: UUID | None = None
    type: ConversationType = ConversationType.CHAT
    status: ConversationStatus | None = None
    output: JsonValue | None = None
    metadata: JsonObject | None = None
    # Diagnostics from the most recent agent run, so a single `conversations get`
    # can explain a failure without separately fetching runs.
    last_run_status: AgentRunStatus | None = None
    last_run_error: str | None = None
    last_run_finished_at: datetime | None = None
    last_run_retryable: bool = False
    messages: list[Message] = Field(default_factory=list)
    agent_runs: list["AgentRun"] = Field(default_factory=list)

    @property
    def is_pod_assistant(self) -> bool:
        return self.agent_id is None

    def next_sequence(self) -> int:
        if not self.messages:
            return 0
        return max(message.sequence for message in self.messages) + 1

    def ordered_messages(self) -> list[Message]:
        return sorted(self.messages, key=lambda message: message.sequence)


class AgentRun(Entity):
    """Internal execution record for one harness pass."""

    conversation_id: UUID
    agent_id: UUID | None = None
    parent_run_id: UUID | None = None
    status: AgentRunStatus = AgentRunStatus.RUNNING
    agent_runtime: AgentRuntimeConfig = Field(
        default_factory=lambda: AgentRuntimeConfig(profile_id="system:lemma")
    )
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    output_data: JsonValue | None = None
    metadata: JsonObject | None = None
    messages: list[Message] = Field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.status in {
            AgentRunStatus.RUNNING,
            AgentRunStatus.STOP_REQUESTED,
        }

    @property
    def is_safely_retryable(self) -> bool:
        return self.status == AgentRunStatus.FAILED and bool(self.messages) and all(
            message.role == MessageRole.USER.value for message in self.messages
        )

    def ordered_messages(self) -> list[Message]:
        return sorted(self.messages, key=lambda message: message.sequence)
