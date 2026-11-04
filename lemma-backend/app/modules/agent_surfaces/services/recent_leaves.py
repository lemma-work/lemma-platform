"""Who left a group in the last few minutes.

A platform's updates about a group are worked as separate items, retried on
failure, in no promised order -- so somebody's join can be worked after the
leave that followed it, and would seat somebody who is gone. A leave is kept
here for ten minutes (``RECENT_LEAVES``); a join of the same account in that
time is not taken as news, and they are seated when they next speak.

Best effort: a store that cannot be reached remembers nothing and refuses
nothing, so a join then seats as it would have.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.caches import RECENT_LEAVES, surface_caches

logger = get_logger(__name__)

_STORE_ERRORS = (RedisError, OSError)


def _key(source_id: UUID, external_user_id: str) -> str:
    return f"{source_id}:{external_user_id}"


async def note_left(source_id: UUID, external_user_id: str | None) -> None:
    """The platform says this account left the place. Never raises."""
    if not external_user_id:
        return
    try:
        await surface_caches.get(RECENT_LEAVES).set_json(
            _key(source_id, external_user_id), True
        )
    except _STORE_ERRORS:
        logger.warning("agent_surfaces.recent_leaves.not_noted.degraded", exc_info=True)


async def left_lately(source_id: UUID, external_user_id: str | None) -> bool:
    """Whether this account left the place in the last few minutes."""
    if not external_user_id:
        return False
    try:
        return bool(
            await surface_caches.get(RECENT_LEAVES).get_json(
                _key(source_id, external_user_id)
            )
        )
    except _STORE_ERRORS:
        logger.warning("agent_surfaces.recent_leaves.not_read.degraded", exc_info=True)
        return False
