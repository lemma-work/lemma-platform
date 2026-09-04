"""Transient realtime publishing for agent conversation streams."""

from __future__ import annotations

from uuid import UUID

from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import (
    get_channel_service,
)
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)

#: Every token, message and terminal frame a watching client sees goes through
#: `publish_conversation_event`. When the channel is down the user-visible
#: symptom is "the agent never answers" while runs complete normally in the
#: database -- and at `logger.debug`, production (LOG_LEVEL=INFO) had nothing at
#: all to distinguish that from a quiet day. One degraded/recovered pair, which
#: is what the volume exemption in `docs/development.md` actually permits.
_publish_incident = DependencyIncident("agent.realtime.publish", logger=logger)


def conversation_channel(conversation_id: UUID) -> str:
    return f"agent:conversation:{conversation_id}"


async def publish_conversation_event(
    conversation_id: UUID,
    payload: dict[str, object],
    *,
    channel_service: RealtimeChannel | None = None,
) -> None:
    """Publish a best-effort transient event to active SSE subscribers."""

    try:
        service = channel_service or await get_channel_service()
        await service.publish(conversation_channel(conversation_id), payload)
    except Exception as exc:
        logger.debug(
            "agent.realtime.publishing_agent_realtime_event.diagnostic",
            conversation_id=str(conversation_id),
            error_type=type(exc).__name__,
        )
        _publish_incident.record_failure(error_type=type(exc).__name__)
    else:
        _publish_incident.record_success()


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


def error_payload(agent_run_id: UUID, error: str) -> dict[str, object]:
    return {
        "type": "error",
        "agent_run_id": str(agent_run_id),
        "data": error,
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
