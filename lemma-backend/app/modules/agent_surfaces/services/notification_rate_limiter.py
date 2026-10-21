"""Ceilings on how often a pod reaches people outside the app.

The expected failure this guards is not malice, it is a badly-prompted agent in
a retry loop putting forty messages on a colleague's phone in a minute. Schedules
already have a circuit breaker for their version of this; the send path had
nothing, and PR #264 modelled the limit but never enforced it.

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

DEFAULT_MAX_PER_RECIPIENT_PER_HOUR = 20
_WINDOW_SECONDS = 3600

# Every pod with an auto-provisioned mailbox sends from the *same* verified
# domain, so one pod blasting mail spends a reputation all of them share. The
# per-recipient limit above does not bound that at all: a runaway agent
# messaging five hundred different people never trips it. This does.
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


class PushBudget:
    """How often one pod may push a line to one person's phone in an hour.

    Never a refusal of the line: it is in their channel whatever this says.
    What it bounds is the platform push -- a badly-prompted agent in a retry
    loop putting forty messages on a colleague's phone in a minute.
    """

    def __init__(self, *, limit: int = DEFAULT_MAX_PER_RECIPIENT_PER_HOUR, redis=None):
        self._limit = limit
        self._window = _Window(redis)

    async def allows(self, *, pod_id: UUID, recipient_user_id: UUID) -> bool:
        count = await self._window.count(
            f"notify:rate:{pod_id}:{recipient_user_id}", _WINDOW_SECONDS
        )
        if count is not None and count > self._limit:
            logger.warning(
                "agent_surfaces.notification_rate_limiter.exceeded.degraded",
                pod_id=str(pod_id),
                recipient_user_id=str(recipient_user_id),
                limit=self._limit,
            )
            return False
        return True
