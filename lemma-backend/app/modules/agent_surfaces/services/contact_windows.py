"""How often the contact path may say something, or tell somebody something.

Each of these is a message the pod sends without a model deciding to -- a
refusal, a "a person will reply", an inbox note. Left uncounted, each can be
made to repeat by whoever writes to the bot, so each has a window in Redis.
(How often a member may write to a contact is the contact's outreach rule,
in ``contacts``.)

All of them fail **closed**: when Redis is away the message is not sent and the
note not left. Every one of them is either a courtesy the sender can do without
or a copy of what is already in the conversation, and a flood of them is the
worse failure.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger

logger = get_logger(__name__)

DAY = 86_400
HOUR = 3600


class ContactWindows:
    def __init__(self, *, redis=None) -> None:
        self._redis = redis

    async def first_in(self, key: str, seconds: int) -> bool:
        """Whether this is the first claim on ``key`` in its window."""
        try:
            claimed = await (self._redis or get_redis()).set(
                key, "1", ex=seconds, nx=True
            )
        except (RedisError, OSError) as exc:
            _unavailable(exc)
            return False
        return bool(claimed)

    async def count(self, key: str, seconds: int) -> int | None:
        """Count one more in ``key``'s window; ``None`` when Redis is away."""
        try:
            return await incr_with_ttl(self._redis or get_redis(), key, seconds)
        except (RedisError, OSError) as exc:
            _unavailable(exc)
            return None

    async def stranger_refusal(self, surface_id: UUID, sender: str) -> bool:
        """The one refusal a stranger gets from a bot for known contacts, a day."""
        return await self.first_in(
            f"contact:refused:{surface_id}:{sender.lower()}", DAY
        )

    async def person_will_reply(self, conversation_id: UUID) -> bool:
        """The one "a person will reply" a held conversation gets, a day."""
        return await self.first_in(f"contact:held:{conversation_id}", DAY)

    async def parked_sender(self, surface_id: UUID, sender: str) -> bool:
        """The first unverified mail from this sender to this bot, this hour."""
        return await self.first_in(
            f"contact:parked:{surface_id}:{sender.lower()}", HOUR
        )

    async def parked_note(self, surface_id: UUID) -> int | None:
        """Count one more inbox note about unverified mail to this bot, this hour."""
        return await self.count(f"contact:parked_notes:{surface_id}", HOUR)


def _unavailable(exc: BaseException) -> None:
    logger.warning(
        "agent_surfaces.contact_windows.unavailable.degraded",
        error_type=type(exc).__name__,
    )
