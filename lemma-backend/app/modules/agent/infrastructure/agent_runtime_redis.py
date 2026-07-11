"""Redis keys and adapters for cross-process agent runtime coordination."""

from __future__ import annotations

import json
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.modules.agent.domain.value_objects import JsonObject

_DAEMON_CAPACITY_TTL_SECONDS = 120
_redis_client: Redis | None = None


def daemon_command_channel(daemon_id: UUID) -> str:
    return f"agent-runtime:daemon:{daemon_id}:commands"


def run_event_channel(agent_run_id: UUID) -> str:
    return f"agent-runtime:run:{agent_run_id}:events"


def daemon_online_key(daemon_id: UUID) -> str:
    return f"agent-runtime:daemon:{daemon_id}:online"


def _daemon_capacity_key(daemon_id: UUID) -> str:
    return f"agent-runtime:daemon:{daemon_id}:capacity"


def get_agent_runtime_redis() -> Redis:
    global _redis_client  # noqa: PLW0603
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=30,
            socket_keepalive=True,
            max_connections=settings.redis_max_connections,
        )
    return _redis_client


async def set_daemon_capacity(
    *, daemon_id: UUID, active_run_count: int, max_concurrent_runs: int
) -> None:
    await get_agent_runtime_redis().set(
        _daemon_capacity_key(daemon_id),
        json.dumps(
            {
                "active_run_count": active_run_count,
                "max_concurrent_runs": max_concurrent_runs,
            }
        ),
        ex=_DAEMON_CAPACITY_TTL_SECONDS,
    )


async def get_daemon_capacity(*, daemon_id: UUID) -> JsonObject | None:
    raw = await get_agent_runtime_redis().get(_daemon_capacity_key(daemon_id))
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def clear_daemon_capacity(*, daemon_id: UUID) -> None:
    await get_agent_runtime_redis().delete(_daemon_capacity_key(daemon_id))


async def publish_json(channel: str, payload: JsonObject) -> None:
    await (await get_channel_service()).publish(channel, payload)


async def is_daemon_online(*, daemon_id: UUID, user_id: UUID) -> bool:
    value = await get_agent_runtime_redis().get(daemon_online_key(daemon_id))
    return value == str(user_id)


async def close_agent_runtime_redis() -> None:
    global _redis_client  # noqa: PLW0603
    redis_client = _redis_client
    _redis_client = None
    if redis_client is not None:
        await redis_client.aclose()
