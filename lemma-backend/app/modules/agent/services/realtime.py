"""Transient realtime publishing for agent conversation streams."""

from __future__ import annotations

from uuid import UUID

from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import (
    get_channel_service,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


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
        logger.warning(
            "Failed publishing agent realtime event",
            conversation_id=str(conversation_id),
            error_type=type(exc).__name__,
        )


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
    agent_run_id: UUID,
    message: dict[str, object],
) -> dict[str, object]:
    return {
        "type": "message",
        "agent_run_id": str(agent_run_id),
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
