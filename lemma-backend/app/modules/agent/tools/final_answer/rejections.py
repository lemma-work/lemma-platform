"""How many times this run's ``final_answer`` has been sent back, across calls.

The cap on schema rejections is what stops an agent that cannot satisfy its own
output schema from spending the run arguing with the validator. It was a counter
in the toolset's closure, and the MCP bridge assembles a fresh toolset for every
``tools/call`` -- so every call saw a count of one and the cap never tripped.
The count belongs to the run, so it lives in Redis under the run's id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "agent:final_answer:rejections:v1"
# Past any run's wall-clock budget, so a count never outlives its run by much
# and never expires under one still going.
_TTL_SECONDS = 6 * 60 * 60


class RejectionCounter(Protocol):
    async def count_one(self, agent_run_id: UUID) -> int:
        """Record one rejection for the run, and return how many it has had."""
        ...


@dataclass
class RedisRejectionCounter:
    redis: Redis | None = None

    async def count_one(self, agent_run_id: UUID) -> int:
        redis = self.redis or get_redis(url=settings.redis_url)
        key = f"{_KEY_PREFIX}:{agent_run_id}"
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, _TTL_SECONDS)
        count, _ = await pipe.execute()
        return int(count)


@dataclass
class LocalRejectionCounter:
    """For a call with no run to key on, and when Redis cannot be reached:
    counted for as long as this toolset lives, which is the old behaviour."""

    count: int = 0

    async def count_one(self, agent_run_id: UUID | None) -> int:
        del agent_run_id
        self.count += 1
        return self.count


async def count_rejection(
    counter: RejectionCounter,
    fallback: LocalRejectionCounter,
    agent_run_id: UUID | None,
) -> int:
    if agent_run_id is None:
        return await fallback.count_one(None)
    try:
        return await counter.count_one(agent_run_id)
    except RedisError:
        logger.warning(
            "agent.final_answer.rejection_count_unavailable.degraded", exc_info=True
        )
        return await fallback.count_one(agent_run_id)
