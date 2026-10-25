"""What a conversation's live events say: the frames every transport carries.

A run publishes these as it goes -- tokens, messages, status, its ending, a
conversation's new title -- through the one publisher
(``infrastructure/live.py``). The SSE stream, the web app's live socket and a
visitor's page each subscribe to the same publish and encode a frame for their
own wire, once, in the transport; the frames themselves are built here.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.entities import AgentRun, Message
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    JsonObject,
    to_json_value,
)
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


def input_added_payload(
    agent_run_id: UUID,
    message: dict[str, object],
) -> dict[str, object]:
    return {
        "type": "message",
        "agent_run_id": str(agent_run_id),
        "data": message,
    }


def token_payload(
    agent_run_id: UUID,
    token: str,
    *,
    kind: str = "text",
) -> dict[str, object]:
    return {
        "type": "token",
        "kind": kind,
        "agent_run_id": str(agent_run_id),
        "data": token,
    }


def status_payload(
    agent_run_id: UUID,
    data: dict[str, object],
) -> dict[str, object]:
    return {
        "type": "status",
        "agent_run_id": str(agent_run_id),
        "data": data,
    }


def message_payload(
    agent_run_id: UUID | None,
    message: dict[str, object],
) -> dict[str, object]:
    """A message frame. Optional run id, unlike the run-scoped frames here.

    A message can exist without a run -- a tool return closed by the MCP bridge
    outside one, a superseded return replayed from an older turn. Frames are
    routed by conversation, so those still belong on the stream; the field just
    has nothing to say, and used to say the string ``"None"``.
    """
    return {
        "type": "message",
        "agent_run_id": str(agent_run_id) if agent_run_id is not None else None,
        "data": message,
    }


def error_payload(
    agent_run_id: UUID,
    error: str,
    *,
    code: str | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "type": "error",
        "agent_run_id": str(agent_run_id),
        "data": error,
        **({"error_code": code} if code else {}),
        **({"error_reason": reason} if reason else {}),
    }


def completed_payload(
    *,
    conversation_id: UUID,
    agent_run_id: UUID,
    status: str,
    data: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "type": "completed",
        "agent_run_id": str(agent_run_id),
        "data": {
            "conversation_id": str(conversation_id),
            "status": status,
            **(data or {}),
        },
    }


def title_updated_payload(
    conversation_id: UUID,
    title: str,
) -> dict[str, object]:
    """Transient event signalling a conversation's title was (re)generated."""
    return {
        "type": "title",
        "data": {
            "conversation_id": str(conversation_id),
            "title": title,
        },
    }


def run_ending_payload(agent_run: AgentRun) -> dict[str, object] | None:
    """A finished run's ending, as its stream said it -- ``completed``,
    ``error`` or ``stopped`` -- for a client that joins after; ``None`` while
    it runs. What the SSE stream replays, and the live socket too."""
    if agent_run.status == AgentRunStatus.FAILED:
        return {
            "type": "error",
            "agent_run_id": str(agent_run.id),
            "data": agent_run.error or "Agent run failed",
        }
    if agent_run.status not in (AgentRunStatus.STOPPED, AgentRunStatus.COMPLETED):
        return None
    data: dict[str, object] = {
        "conversation_id": str(agent_run.conversation_id),
        "status": agent_run.status.value,
    }
    if agent_run.status == AgentRunStatus.COMPLETED:
        if agent_run.output_data is not None:
            data["output_data"] = agent_run.output_data
        return {"type": "completed", "agent_run_id": str(agent_run.id), "data": data}
    return {"type": "stopped", "agent_run_id": str(agent_run.id), "data": data}
