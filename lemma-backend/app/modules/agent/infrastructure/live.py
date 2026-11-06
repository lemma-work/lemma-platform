"""One publisher for a conversation's live events, and one way to follow them.

A run's tokens, messages, status and ending, and a conversation's new title,
are published here (``LiveEvents.publish``, the port in ``domain/ports``) onto
the conversation's channel, once. Every transport subscribes to that one
publish (``subscribe``) and encodes what it hears for its own wire, once, in the
transport: the SSE stream (``api/controllers/conversation_streaming``), the web
app's live socket (``services/live_conversation``) and a visitor's page
(``contracts/visitor_stream``). The frames themselves are ``domain/live_events``.

Best effort: a publish that fails is recorded, never raised -- the run's
messages are stored either way, and a client that missed frames refetches.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any
from uuid import UUID

from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import (
    get_channel_service,
)
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)

#: Every token, message and terminal frame a watching client sees goes through
#: `publish`. When the channel is down the user-visible symptom is "the agent
#: never answers" while runs complete normally in the database -- and at
#: `logger.debug`, production (LOG_LEVEL=INFO) had nothing at all to distinguish
#: that from a quiet day. One degraded/recovered pair, which is what the volume
#: exemption in `docs/development.md` actually permits.
_publish_incident = DependencyIncident("agent.realtime.publish", logger=logger)


def conversation_channel(conversation_id: UUID) -> str:
    return f"agent:conversation:{conversation_id}"


class ConversationLiveEvents:
    """``domain/ports.LiveEvents`` on the realtime channel service."""

    def __init__(self, channel_service: RealtimeChannel | None = None) -> None:
        self._channel_service = channel_service

    async def _service(self) -> RealtimeChannel:
        return self._channel_service or await get_channel_service()

    async def publish(self, conversation_id: UUID, event: dict[str, object]) -> None:
        """Publish a best-effort transient event to every subscriber."""
        try:
            service = await self._service()
            await service.publish(conversation_channel(conversation_id), event)
        except Exception as exc:
            logger.debug(
                "agent.realtime.publishing_agent_realtime_event.diagnostic",
                conversation_id=str(conversation_id),
                error_type=type(exc).__name__,
            )
            _publish_incident.record_failure(error_type=type(exc).__name__)
        else:
            _publish_incident.record_success()

    @asynccontextmanager
    async def subscribe(
        self, conversation_id: UUID
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        """A lease on one conversation's events, decoded; undecodable ones skipped."""
        service = await self._service()
        async with service.subscribe([conversation_channel(conversation_id)]) as raw:

            async def decoded() -> AsyncIterator[dict[str, Any]]:
                async for message in raw:
                    event = _decoded(message)
                    if event is not None:
                        yield event

            yield decoded()


def _decoded(message: object) -> dict[str, Any] | None:
    """One published event; small (a token, a status), so decoded in place."""
    if isinstance(message, dict):
        return message
    if not isinstance(message, str | bytes | bytearray):
        return None
    try:
        payload = json.loads(message)
    except ValueError, TypeError:
        return None
    return payload if isinstance(payload, dict) else None


@lru_cache(maxsize=1)
def live_events() -> ConversationLiveEvents:
    """The process's publisher, on whatever channel service it is given."""
    return ConversationLiveEvents()


async def publish_conversation_event(
    conversation_id: UUID,
    payload: dict[str, object],
    *,
    channel_service: RealtimeChannel | None = None,
) -> None:
    """``LiveEvents.publish``, for a call site that holds no publisher."""
    publisher = (
        ConversationLiveEvents(channel_service)
        if channel_service is not None
        else live_events()
    )
    await publisher.publish(conversation_id, payload)


__all__ = [
    "ConversationLiveEvents",
    "conversation_channel",
    "live_events",
    "publish_conversation_event",
]
