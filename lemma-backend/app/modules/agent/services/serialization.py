"""Serialization helpers for agent service events."""

from __future__ import annotations

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import JsonObject, to_json_value


def message_to_payload(message: Message) -> JsonObject:
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "sequence": message.sequence,
        "agent_run_id": str(message.agent_run_id) if message.agent_run_id else None,
        # Identity travels on the live frame, not only on the refetch. Without
        # these a streamed message arrives anonymous and the transcript labels
        # it with the default agent until something refetches it.
        "sender_user_id": (
            str(message.sender_user_id) if message.sender_user_id else None
        ),
        "agent_id": str(message.agent_id) if message.agent_id else None,
        "role": message.role,
        "kind": message.kind.value,
        "text": message.text,
        "tool_name": message.tool_name,
        "tool_call_id": message.tool_call_id,
        "tool_args": to_json_value(message.tool_args)
        if message.tool_args is not None
        else None,
        "tool_result": to_json_value(message.tool_result)
        if message.tool_result is not None
        else None,
        "metadata": to_json_value(message.metadata or {}),
        "created_at": message.created_at.isoformat(),
    }
