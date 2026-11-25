"""A conversation whose repository checkout failed, for whoever waits on it.

The checkout is made in the sandbox before the agent's first command
(``github_project``), and a failure leaves the agent in an empty directory with
a notice. For a person's chat that is enough: they read the agent's answer. A
workflow step that named the repository is waiting on the conversation instead,
and an answer written in an empty folder would otherwise end the step green
(``application/automation/outcome``). So a failed checkout is remembered
against the conversation, and a checkout that lands later clears it.

Kept beside the checkout's own marker, in Redis, rather than on the
conversation's row: the checkout runs on the tool path, which holds no unit of
work, and the fact only matters until the step that waits on it settles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis

if TYPE_CHECKING:
    from redis.asyncio import Redis

_KEY_PREFIX = "workspace:github-project:v1:conversation-failed"
#: Longer than a workflow step waits on its agent, so the step that settles
#: still finds it; a conversation that checks out later clears it sooner.
_TTL_SECONDS = 7 * 24 * 60 * 60
#: git's last words are the useful ones, and a step's error is not a log.
_MAX_REASON_CHARS = 500


def _key(conversation_id: UUID) -> str:
    return f"{_KEY_PREFIX}:{conversation_id}"


async def remember_checkout_failure(
    redis: "Redis", conversation_id: UUID, reason: str
) -> None:
    await redis.set(_key(conversation_id), reason[-_MAX_REASON_CHARS:], ex=_TTL_SECONDS)


async def forget_checkout_failure(redis: "Redis", conversation_id: UUID) -> None:
    await redis.delete(_key(conversation_id))


async def checkout_failure(
    conversation_id: UUID, *, redis: "Redis | None" = None
) -> str | None:
    """Why the conversation's checkout failed, or None if it did not, or landed."""
    store = redis or get_redis(url=settings.redis_url)
    value = await store.get(_key(conversation_id))
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


__all__ = [
    "checkout_failure",
    "forget_checkout_failure",
    "remember_checkout_failure",
]
