"""Domain entities for the unified agent module."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import Field

from app.core.authorization.context import ResourceType
from app.core.domain.entity import CreatedEntity, Entity
from app.modules.agent.domain.participants import ConversationParticipant
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
    #: The person who wrote it, when a person did. See the column comment.
    sender_user_id: UUID | None = None
    #: The agent that produced it, read from its run rather than stored. A
    #: conversation can be answered by more than one agent now, so "which agent
    #: said this" is a question the transcript has to be able to answer, and
    #: the run is where it is recorded. None on anything a person wrote.
    agent_id: UUID | None = None
    # An enum, like `kind`. It used to be a bare `str` while `kind` next to it
    # was an enum, so every reader had to know which of the two it was holding
    # and normalize accordingly -- and one that forgot compared `str(kind)`
    # against a value and silently never matched. `MessageRole` subclasses
    # `str`, so `role == "user"` still holds and nothing downstream had to move.
    role: MessageRole
    kind: MessageKind
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: JsonValue | None = None
    tool_result: JsonValue | None = None
    metadata: JsonObject | None = None

    @classmethod
    def create(
        cls,
        *,
        conversation_id: UUID,
        sequence: int,
        agent_run_id: UUID | None,
        role: MessageRole | str,
        sender_user_id: UUID | None = None,
        kind: MessageKind = MessageKind.TEXT,
        text: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        tool_args: JsonValue | None = None,
        tool_result: JsonValue | None = None,
        metadata: JsonObject | None = None,
    ) -> "Message":
        return cls(
            conversation_id=conversation_id,
            sequence=sequence,
            agent_run_id=agent_run_id,
            sender_user_id=sender_user_id,
            role=MessageRole(role),
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
            sender_user_id=draft.sender_user_id,
            role=draft.role,
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
    is_archived: bool = False
    # Diagnostics from the most recent agent run, so a single `conversations get`
    # can explain a failure without separately fetching runs.
    last_run_status: AgentRunStatus | None = None
    last_run_error: str | None = None
    last_run_finished_at: datetime | None = None
    last_run_retryable: bool = False
    messages: list[Message] = Field(default_factory=list)
    agent_runs: list["AgentRun"] = Field(default_factory=list)
    #: Loaded by `ConversationRepository.get_conversation`. Empty on an entity
    #: nobody asked for membership on, which is why `validate_conversation_access`
    #: still accepts the owner directly rather than relying on this alone.
    participants: list[ConversationParticipant] = Field(default_factory=list)

    def has_participant(self, user_id: UUID) -> bool:
        return any(participant.user_id == user_id for participant in self.participants)

    def has_agent_participant(self, agent_id: UUID) -> bool:
        return any(
            participant.agent_id == agent_id for participant in self.participants
        )

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
    #: Whose message started this run. See the column comment; it is what makes
    #: a run's working attributable in a conversation with several people in it.
    triggered_by_user_id: UUID | None = None
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
    #: How many messages the run actually has, which is not always how many are
    #: loaded. Runtime history loads older runs down to their first and last
    #: message, so anything reasoning about the *size* of a run -- the history
    #: budget, the elision count -- must ask this rather than len(messages).
    #: None means nothing was elided and the two are the same.
    total_message_count: int | None = None
    #: Newest message timestamp, carried when the messages themselves are not.
    #: The surface age window asks a run how recently it was active, and it has
    #: to be able to ask that before deciding which runs are worth loading.
    newest_message_at: datetime | None = None

    @property
    def message_count(self) -> int:
        """Messages in the run, whether or not they were all loaded."""
        return (
            self.total_message_count
            if self.total_message_count is not None
            else len(self.messages)
        )

    @property
    def is_active(self) -> bool:
        return self.status in {
            AgentRunStatus.RUNNING,
            AgentRunStatus.STOP_REQUESTED,
        }

    @property
    def is_safely_retryable(self) -> bool:
        return (
            self.status == AgentRunStatus.FAILED
            and bool(self.messages)
            and all(message.role is MessageRole.USER for message in self.messages)
        )

    def ordered_messages(self) -> list[Message]:
        return sorted(self.messages, key=lambda message: message.sequence)
