"""Realtime channels for Agent Host dispatch and run streaming.

Durable protocol state lives in PostgreSQL; these Redis pub/sub channels are
the lossy fast lane. A poke wakes a long-polling host when a command is
enqueued (the 25s poll deadline is the fallback), and the per-run stream
channel carries cosmetic chunk events that are never journaled.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import JsonObject


logger = get_logger(__name__)


def host_poke_channel(host_id: UUID) -> str:
    return f"agent-host:host:{host_id}:poke"


def run_stream_channel(run_id: UUID) -> str:
    return f"agent-host:run:{run_id}:stream"


async def poke_host(host_id: UUID) -> None:
    """Best-effort wake-up for a long-polling host; never raises."""
    await _publish(host_poke_channel(host_id), {"type": "poke"})


async def publish_run_stream_event(run_id: UUID, event: JsonObject) -> None:
    """Publish one cosmetic stream event; loss is repaired by upserts."""
    await _publish(run_stream_channel(run_id), event)


async def _publish(channel: str, payload: JsonObject) -> None:
    try:
        service = await get_channel_service()
        await service.publish(channel, payload)
    except (RedisError, RuntimeError, OSError):
        logger.debug(
            "agent.infrastructure.agent_host_channels.publish_skipped",
            channel=channel,
            exc_info=True,
        )
