"""Domain entities for the unified agent module."""

from __future__ import annotations

from dataclasses import dataclass

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import Field

from app.core.authorization.context import ResourceType
from app.core.authorization.delegation import is_pod_default_agent
from app.core.domain.entity import CreatedEntity, Entity
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.conversation_metadata import (
    MANUAL_RETRY_SOURCE,
    source_of,
)
from app.modules.agent.domain.errors import AgentValidationError
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


MAX_AGENT_INSTRUCTION_CHARACTERS = 60_000

#: What one message a person sends may hold. The same budget as an agent's
#: instruction: both are text replayed into every model request that follows.
MAX_MESSAGE_CHARACTERS = MAX_AGENT_INSTRUCTION_CHARACTERS


def validate_agent_instruction(instruction: str | None) -> str:
    """Validate authored text without rejecting historical entities on read."""
    if instruction is None or not instruction.strip():
        raise AgentValidationError("Agent instruction is required")
    if len(instruction) > MAX_AGENT_INSTRUCTION_CHARACTERS:
        raise AgentValidationError(
            f"Agent instruction must be at most {MAX_AGENT_INSTRUCTION_CHARACTERS:,} characters"
        )
    return instruction


class Agent(Entity):
    """Reusable agent definition.

    Every agent is pod-owned and every agent has a row, the pod's own assistant
    included -- ``kind`` says which of them nobody created. It used to be the
    one exception, synthesised on the way past with no row behind it, which is
    why so much of this module still asks "is there an agent?" when it means
    "is this the pod's own".
    """

    resource_type: ClassVar[ResourceType] = ResourceType.AGENT

    pod_id: UUID
    user_id: UUID
    name: str
    kind: AgentKind = AgentKind.USER
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
    """Primary storage aggregate for pod assistant and pod agent chats.

    A conversation is one agent working for one principal. That is the member
    ``user_id`` names -- unless ``contact_id`` is set, when it works for that
    contact, and ``user_id`` is the member who looks after the conversation:
    whom it may pass a question to, never whose access it has.
    """

    user_id: UUID
    pod_id: UUID
    #: The contact this conversation answers. Written by the server when it
    #: opens the conversation, and by a merge; never by anything a client sends.
    contact_id: UUID | None = None
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
    #: None only before the row is written.
    last_activity_at: datetime | None = None
    # Diagnostics from the most recent agent run, so a single `conversations get`
    # can explain a failure without separately fetching runs.
    last_run_status: AgentRunStatus | None = None
    last_run_error: str | None = None
    last_run_error_code: str | None = None
    last_run_error_reason: str | None = None
    last_run_finished_at: datetime | None = None
    last_run_retryable: bool = False
    #: A pause here is waiting on the owner. Read-side only, like the
    #: diagnostics above: set by the reads that answer a person, not stored.
    has_pending_interaction: bool = False
    #: WAITING on the agent's own work (a sub-agent, a process, a timer), with
    #: a run to come. Read-side only, set where one conversation is read.
    waiting_on_work: bool = False
    messages: list[Message] = Field(default_factory=list)
    agent_runs: list["AgentRun"] = Field(default_factory=list)

    @property
    def is_pod_assistant(self) -> bool:
        """Whether the pod's own assistant answers here.

        This drives which base prompt the run is built from, so reading it
        wrongly does not raise -- it quietly makes the assistant a different
        agent. Which is why it delegates rather than testing ``agent_id is
        None`` in place: a conversation now names the assistant by its row, and
        older rows still name it by naming nobody.
        """
        return is_pod_default_agent(self.agent_id, pod_id=self.pod_id)

    def next_sequence(self) -> int:
        if not self.messages:
            return 0
        return max(message.sequence for message in self.messages) + 1

    def ordered_messages(self) -> list[Message]:
        return sorted(self.messages, key=lambda message: message.sequence)


def replays_without_effect(role: MessageRole, kind: MessageKind) -> bool:
    """Whether a failed run holding this message can be run again as it was.

    The person's own messages are what a retry replays. The model's reasoning
    is the one thing it says that does nothing: no text reached anyone and no
    tool ran, so a run that failed after only thinking is as safe to retry as
    one that failed before the model answered at all. The retry does not carry
    the old reasoning forward -- a thought with no answer after it is never
    replayed to the model.
    """
    return role is MessageRole.USER or (
        role is MessageRole.ASSISTANT and kind is MessageKind.THINKING
    )


class AgentRun(Entity):
    """Internal execution record for one harness pass."""

    conversation_id: UUID
    agent_id: UUID | None = None
    parent_run_id: UUID | None = None
    status: AgentRunStatus = AgentRunStatus.RUNNING
    # Required: a run records the runtime it was started on, and a default here
    # would quietly read "system" for a run whose creator forgot to say.
    agent_runtime: AgentRuntimeConfig
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    output_data: JsonValue | None = None
    metadata: JsonObject | None = None
    messages: list[Message] = Field(default_factory=list)
    #: How many messages the run actually has, which is not always how many are
    #: loaded. Runtime history loads older runs down to their first and last
    #: message, so anything reasoning about the *size* of a run -- the elision
    #: count -- must ask this rather than len(messages).
    #: None means nothing was elided and the two are the same.
    total_message_count: int | None = None

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
    def replays_a_retried_turn(self) -> bool:
        """Whether this run was started by a retry, and so replays a person's
        turn that belongs to the run it retried rather than to itself."""
        return source_of(self.metadata) == MANUAL_RETRY_SOURCE

    @property
    def is_safely_retryable(self) -> bool:
        # A retry owns no user message, so "it holds the person's turn" is
        # answered by what started it. Its retry was only allowed because the
        # run before it was replay-safe, so the same turn is still there.
        return (
            self.status == AgentRunStatus.FAILED
            and (
                self.replays_a_retried_turn
                or any(message.role is MessageRole.USER for message in self.messages)
            )
            and all(
                replays_without_effect(message.role, message.kind)
                for message in self.messages
            )
        )

    def ordered_messages(self) -> list[Message]:
        return sorted(self.messages, key=lambda message: message.sequence)


@dataclass(frozen=True, slots=True)
class RuntimeHistoryWindow:
    """The runs a prompt can carry, and the two facts a window cannot answer itself.

    A windowed list does not know what it was cut from, and the notice telling
    the model that older exchanges exist is built from exactly that -- so the
    total comes back with it. And the run being resumed may be older than the
    window: the runner needs it either way, and refusing it would turn a missing
    notice into a failed run.

    See ``infrastructure/runtime_history_window`` for the statements.
    """

    #: The newest runs, oldest-first -- the order every trim downstream assumes.
    runs: list["AgentRun"]
    #: Every run the conversation has, windowed or not.
    total_runs: int
    #: The run being resumed, whether or not it survived the window.
    current_run: "AgentRun | None"

    @property
    def dropped_by_window(self) -> int:
        return self.total_runs - len(self.runs)
