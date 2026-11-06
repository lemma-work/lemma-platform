"""The ceiling on how much mail a pod sends from the shared domain.

Pushes to a member's phone have no ceiling of their own: one channel pushes a
member at most once per push window (``channels``' ``push_windows``) however
many plain lines it hears, so a runaway agent writing forty lines in a minute
is one push, not forty. A line that names or asks them goes at once -- as it
did past the hourly ceiling this module once kept, which never held those.

Redis rather than an in-process counter, deliberately: the API, the worker and
the scheduler are separate processes, and a per-process counter would let each
of them spend the whole budget.

A fixed window, not a sliding one. The imprecision at a window boundary (up to
2x the limit across two adjacent windows) is irrelevant for a bound whose job is
to stop a runaway loop, and a fixed window is one INCR instead of a sorted-set
read-modify-write on every send.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError

logger = get_logger(__name__)

# Every pod with an auto-provisioned mailbox sends from the *same* verified
# domain, so one pod blasting mail spends a reputation all of them share. A
# push window bounds what one channel sends one person, not that: a runaway
# agent messaging five hundred different people never trips it. This does.
#
# Generous on purpose. It is a ceiling on absurdity, not a quota — a pod doing
# real work with a few dozen colleagues will never see it, and a pod that does
# see it is doing something nobody asked for.
DEFAULT_MAX_EMAILS_PER_POD_PER_DAY = 200
_DAY_SECONDS = 86_400


class EmailSendLimitExceeded(AgentSurfaceError):
    """Running out of email budget is a delivery outcome -- the line is still
    in the person's channel -- so it reads as a reason it was not emailed."""

    def __init__(self, limit: int):
        super().__init__(
            message=(
                f"This pod has already sent {limit} emails today. It is in "
                "their channel in Lemma."
            ),
            code="EMAIL_SEND_LIMIT_EXCEEDED",
            status_code=429,
        )


class _Window:
    def __init__(self, redis=None) -> None:
        self._redis = redis

    async def count(self, key: str, ttl: int) -> int | None:
        """Increment a fixed window, or None when Redis could not be reached.

        Fails **open**: a ceiling on a runaway loop is not worth everybody
        hearing nothing while Redis is down.
        """
        client = self._redis or get_redis()
        try:
            return await incr_with_ttl(client, key, ttl)
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent_surfaces.notification_rate_limiter.unavailable.degraded",
                error=str(exc),
            )
            return None


class EmailBudget:
    """How many emails one pod may send a day from the shared domain."""

    def __init__(self, *, limit: int = DEFAULT_MAX_EMAILS_PER_POD_PER_DAY, redis=None):
        self._limit = limit
        self._window = _Window(redis)

    async def check(self, *, pod_id: UUID) -> None:
        """Count this outbound email against the pod's day, and refuse over it."""
        count = await self._window.count(f"notify:rate:email:{pod_id}", _DAY_SECONDS)
        if count is not None and count > self._limit:
            logger.warning(
                "agent_surfaces.notification_rate_limiter.email_exceeded.degraded",
                pod_id=str(pod_id),
                limit=self._limit,
            )
            raise EmailSendLimitExceeded(self._limit)
