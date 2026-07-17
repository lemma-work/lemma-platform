"""Persists harness-emitted messages and derives terminal run output.

Keeps the flat-message persistence rules (final-answer tagging, output
extraction, conversation-status derivation) in one place so the runner stays
focused on orchestration.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    ConversationStatus,
    JsonObject,
    JsonValue,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository


class RunMessageWriter:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def persist(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        data: object,
    ) -> Message:
        if isinstance(data, MessageDraft):
            draft = data
        elif isinstance(data, dict) and isinstance(data.get("text"), str):
            # Harness MESSAGE events carry the role/kind/text/metadata shape;
            # persist only the unwrapped text plus its role/kind/metadata,
            # not str(dict) which would store the entire JSON envelope as
            # the visible bubble text.
            draft = MessageDraft(
                role=data.get("role") or MessageRole.ASSISTANT,
                kind=data.get("kind") or MessageKind.TEXT,
                text=data.get("text"),
                tool_name=data.get("tool_name"),
                tool_call_id=data.get("tool_call_id"),
                tool_args=data.get("tool_args"),
                tool_result=data.get("tool_result"),
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
            )
        else:
            draft = MessageDraft.of_text(str(data))

        metadata: JsonObject = dict(draft.metadata or {})
        metadata.pop("author_user_id", None)
        metadata.pop("agent_run_id", None)
        if (
            draft.role == MessageRole.ASSISTANT
            and draft.kind == MessageKind.TEXT
            and "is_final_answer" not in metadata
        ):
            metadata["is_final_answer"] = True
        draft = draft.model_copy(update={"metadata": metadata})

        async with self.uow_factory() as uow:
            return await ConversationRepository(uow).append_message(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                draft=draft,
            )

    def output_data_from_event(self, event: AgentEvent) -> JsonValue | None:
        """Extract terminal output from a MESSAGE event, if present."""

        data = event.data
        if not isinstance(data, MessageDraft):
            return None

        metadata = data.metadata or {}
        if metadata.get("is_final_answer") is False:
            return None
        is_assistant_text = (
            data.role == MessageRole.ASSISTANT and data.kind == MessageKind.TEXT
        )
        if not metadata.get("is_final_answer") and not is_assistant_text:
            return None

        structured_output = metadata.get("structured_output")
        if metadata.get("is_final_answer") and structured_output is not None:
            return structured_output
        if structured_output is not None:
            if isinstance(structured_output, dict):
                return structured_output
            if isinstance(structured_output, str):
                return {"answer": structured_output}
            return {"result": structured_output}

        if is_assistant_text:
            return {"answer": data.text or ""}
        return None

    def final_status_from_event(
        self,
        event: AgentEvent,
    ) -> tuple[ConversationStatus | None, str | None]:
        data = event.data
        if not isinstance(data, MessageDraft):
            return None, None
        metadata = data.metadata or {}
        raw_status = metadata.get("final_answer_status")
        if raw_status is None:
            return None, None
        error = metadata.get("final_answer_error")
        return ConversationStatus(str(raw_status)), str(error) if error else None
