"""Serialization helpers for agent service events."""

from __future__ import annotations

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.domain.wire_metadata import message_wire_metadata


def message_to_payload(message: Message) -> JsonObject:
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "sequence": message.sequence,
        "agent_run_id": str(message.agent_run_id) if message.agent_run_id else None,
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
        "metadata": to_json_value(message_wire_metadata(message)),
        "created_at": message.created_at.isoformat(),
    }
