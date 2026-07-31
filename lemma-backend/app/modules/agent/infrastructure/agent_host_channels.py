"""Wake-up channel for long-polling Agent Hosts.

The poke is a pure latency optimization: a host's 25s long-poll deadline and
the idle re-query already bound how long an enqueued command waits, so a lost
poke costs a few seconds and never correctness. Publishing is therefore best
effort and never raises into the caller.

Run events do not travel here. They go to the run's Redis Stream, which is
ordered and replayable; this channel has neither property.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.log.log import get_logger


logger = get_logger(__name__)


def host_poke_channel(host_id: UUID) -> str:
    return f"agent-host:host:{host_id}:poke"


async def poke_host(host_id: UUID) -> None:
    """Best-effort wake-up for a long-polling host; never raises."""
    try:
        service = await get_channel_service()
        await service.publish(host_poke_channel(host_id), {"type": "poke"})
    except (RedisError, RuntimeError, OSError):
        logger.debug(
            "agent.infrastructure.agent_host_channels.poke_skipped",
            host_id=str(host_id),
            exc_info=True,
        )
