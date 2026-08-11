"""A ceiling on how often one pod may message one person.

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


class NotificationRateLimitExceeded(AgentSurfaceError):
    def __init__(self, limit: int):
        super().__init__(
            message=(
                f"This pod has already sent {limit} notifications to this person "
                "in the past hour."
            ),
            code="NOTIFICATION_RATE_LIMIT_EXCEEDED",
            status_code=429,
        )


class EmailSendLimitExceeded(AgentSurfaceError):
    """Distinct from the per-recipient limit: different budget, different fix.

    Not raised out of ``notify``. Running out of email budget is a *delivery*
    outcome — the notification is still real and still in the person's inbox —
    so it reads as an undeliverable reason rather than a failed tool call.
    """

    def __init__(self, limit: int):
        super().__init__(
            message=(
                f"This pod has already sent {limit} emails today. The "
                "notification is in their Lemma inbox."
            ),
            code="EMAIL_SEND_LIMIT_EXCEEDED",
            status_code=429,
        )


class NotificationRateLimiter:
    def __init__(
        self,
        *,
        limit: int = DEFAULT_MAX_PER_RECIPIENT_PER_HOUR,
        email_limit: int = DEFAULT_MAX_EMAILS_PER_POD_PER_DAY,
        redis=None,
    ):
        self._limit = limit
        self._email_limit = email_limit
        self._redis = redis

    async def _count(self, key: str, ttl: int) -> int | None:
        """Increment a fixed window, or None when Redis could not be reached.

        Fails **open** — see ``check``. One INCR plus a first-write EXPIRE, so a
        window costs a single round trip after it opens.
        """
        client = self._redis or get_redis()
        try:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, ttl)
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent_surfaces.notification_rate_limiter.unavailable.degraded",
                error=str(exc),
            )
            return None
        return count

    async def check_email(self, *, pod_id: UUID) -> None:
        """Count this outbound email against the pod's day, and refuse over it.

        Called at the point of an email send rather than in ``notify``: a
        notification that lands on Slack costs the shared sending domain
        nothing, and should not spend a budget that exists to protect it.
        """
        count = await self._count(f"notify:rate:email:{pod_id}", _DAY_SECONDS)
        if count is not None and count > self._email_limit:
            logger.warning(
                "agent_surfaces.notification_rate_limiter.email_exceeded.degraded",
                pod_id=str(pod_id),
                limit=self._email_limit,
            )
            raise EmailSendLimitExceeded(self._email_limit)

    async def check(self, *, pod_id: UUID, recipient_user_id: UUID) -> None:
        """Count this send, and refuse it if the hour's budget is spent.

        Fails **open** if Redis is unreachable — and *only* then: a bug in this
        method should surface, not silently disable the limiter. The limiter exists to bound a
        runaway agent, and trading "some pods can be spammed while Redis is down"
        for "nobody can be told anything while Redis is down" is the wrong side
        to fail on for a notification system whose entire premise is that people
        get told.
        """
        # Pod-scoped: two pods messaging the same person are two different
        # relationships, and one pod's runaway loop should not mute the other.
        count = await self._count(
            f"notify:rate:{pod_id}:{recipient_user_id}", _WINDOW_SECONDS
        )
        if count is not None and count > self._limit:
            logger.warning(
                "agent_surfaces.notification_rate_limiter.exceeded.degraded",
                pod_id=str(pod_id),
                recipient_user_id=str(recipient_user_id),
                limit=self._limit,
            )
            raise NotificationRateLimitExceeded(self._limit)
