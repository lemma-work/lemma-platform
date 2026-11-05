"""Use-case slices of ``ports.ConversationRepository``.

The repository port declares everything the implementation exposes, so a
caller typed against it can reach all of it. A collaborator that needs two of
those methods says so here instead, and a reader of its signature knows what it
touches. Every slice is a subset of the full port with the same signatures --
``test_agent_ports_contract`` holds them to it -- so the one repository satisfies
each, and a slice cannot drift from what it slices.

Separate Protocols rather than the full port assembled from them: a port built
by inheriting ten others is the mixin pile the architecture ratchet counts
against, and the slices here are the ones a caller actually narrows to.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    ConversationStatus,
    JsonObject,
    MessageDraft,
)


class ConversationLookup(Protocol):
    """Loading the conversation a request names."""

    async def get_conversation(
        self,
        conversation_id: UUID,
        *,
        include_runs: bool = False,
    ) -> Conversation | None: ...


class ConversationStatusWrite(Protocol):
    """Moving a conversation's status where no run is being finished."""

    async def set_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
    ) -> None: ...


class QueuedMessages(Protocol):
    """Messages sent while a run was busy: how many, and whose."""

    async def count_queued_user_messages(
        self,
        agent_run_id: UUID,
        *,
        notes: bool | None = None,
        after_sequence: int | None = None,
    ) -> int: ...

    async def earliest_queued_is_note(
        self, agent_run_id: UUID, *, after_sequence: int | None = None
    ) -> bool | None: ...


class PausesHoldingATurn(Protocol):
    """The pauses a new message has to wait behind."""

    async def pausing_calls_decided_awaiting_a_return(
        self,
        *,
        conversation_id: UUID,
        pausing_tool_names: Sequence[str],
    ) -> list[Message]: ...

    async def pausing_calls_awaiting_a_decision(
        self,
        *,
        conversation_id: UUID,
        pausing_tool_names: Sequence[str],
        limit: int | None = None,
    ) -> list[Message]: ...


class PauseSupersession(Protocol):
    """Closing the pauses a person moved past: find them, deny them, write the return."""

    async def pausing_calls_awaiting_a_decision(
        self,
        *,
        conversation_id: UUID,
        pausing_tool_names: Sequence[str],
        limit: int | None = None,
    ) -> list[Message]: ...

    async def get_tool_return(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> Message | None: ...

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

    async def append_message(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None,
        draft: MessageDraft,
    ) -> Message: ...


__all__ = [
    "ConversationLookup",
    "ConversationStatusWrite",
    "QueuedMessages",
    "PausesHoldingATurn",
    "PauseSupersession",
]
