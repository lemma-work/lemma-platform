"""How often people outside a pod may put its bot to work.

A member's question spends the pod's model budget on the pod's own business. A
stranger's spends it on theirs, in a chat where one person can keep asking, or
where somebody the pod never met added the bot. So there are two ceilings: one
per person per group, which stops one person looping, and one per group per day,
which caps what a whole group can cost.

Fixed windows in Redis, for the reasons ``notification_rate_limiter`` gives. It
fails **closed**, where that limiter fails open: that one guards colleagues, and
losing a message to a colleague is worse than a burst. This guards strangers,
and a stranger going unanswered while Redis is away costs nothing -- their
message is still in the group for a member to answer.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings

logger = get_logger(__name__)

_TEN_MINUTES = 600
_DAY = 86_400


class OutsiderTurnLimiter:
    def __init__(self, *, redis=None) -> None:
        self._redis = redis

    async def allow(self, *, group_id: UUID, sender_external_id: str) -> bool:
        """Count this turn and say whether it may run.

        The group's day is charged only for turns that will run. A person over
        their own limit is refused before it, so one person looping cannot use
        up everybody else's allowance in the group.
        """
        client = self._redis or get_redis()
        person_limit = surface_settings.surface_outsider_turns_per_person_per_10_minutes
        group_limit = surface_settings.surface_outsider_turns_per_group_per_day
        try:
            per_person = await incr_with_ttl(
                client, f"outsider:turns:{group_id}:{sender_external_id}", _TEN_MINUTES
            )
            if per_person > person_limit:
                self._refused(group_id, per_person=True)
                return False
            per_group = await incr_with_ttl(client, f"outsider:turns:{group_id}", _DAY)
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent_surfaces.outsider_limits.unavailable.degraded",
                error_type=type(exc).__name__,
            )
            return False
        if per_group > group_limit:
            self._refused(group_id, per_person=False)
            return False
        return True

    @staticmethod
    def _refused(group_id: UUID, *, per_person: bool) -> None:
        logger.info(
            "agent_surfaces.outsider_limits.exceeded.observed",
            group_id=str(group_id),
            per_person=per_person,
            per_group=not per_person,
        )
