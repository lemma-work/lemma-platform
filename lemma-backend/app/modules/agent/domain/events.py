"""Domain events for agent orchestration and streaming."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from app.core.domain.events import DomainEvent
from app.modules.agent.domain.value_objects import AgentRunStatus, JsonObject

AGENT_EVENTS_STREAM = "agent_events"


class AgentDomainEvent(DomainEvent):
    _stream_name: ClassVar[str] = AGENT_EVENTS_STREAM

    @classmethod
    def stream_name(cls) -> str:
        return cls._stream_name


class AgentCreatedEvent(AgentDomainEvent):
    event_type: str = "agent.created"
    agent_id: UUID
    pod_id: UUID
    user_id: UUID | None = None
    #: The count, not the names. A toolset name is a product noun that would be
    #: bucketed away downstream anyway, and the shape of the distribution is
    #: what a decision about the tool picker needs.
    tool_count: int = 0


class ConversationStartedEvent(AgentDomainEvent):
    event_type: str = "agent.conversation.started"
    conversation_id: UUID
    pod_id: UUID
    user_id: UUID
    #: Absent for the pod's default assistant, which is the distinction
    #: `is_assistant` reports downstream.
    agent_id: UUID | None = None
    #: Sub-agent spawns and workflow agent nodes both create conversations, and
    #: they scale with traffic rather than with building. Carried so the
    #: consumer can exclude them rather than guessing from the shape.
    parent_id: UUID | None = None


class AgentRunStartedEvent(AgentDomainEvent):
    event_type: str = "agent.run.started"
    conversation_id: UUID
    agent_run_id: UUID
    user_id: UUID
    pod_id: UUID
    agent_name: str | None = None


class AgentRunStopRequestedEvent(AgentDomainEvent):
    event_type: str = "agent.run.stop_requested"
    conversation_id: UUID
    agent_run_id: UUID
    user_id: UUID


class AgentRunCompletedEvent(AgentDomainEvent):
    event_type: str = "agent.run.completed"
    conversation_id: UUID
    agent_run_id: UUID
    status: AgentRunStatus
    data: JsonObject | None = None
