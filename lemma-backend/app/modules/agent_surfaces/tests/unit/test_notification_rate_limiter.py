"""Bounding what an agent can send, in the two ways it can run away.

The limiter had no tests at all — which is how it came to be modelled in #264
and left unenforced until #316. Both windows are covered here, including the
Redis-is-down behaviour, because "fails open" is a decision that should break a
test if someone quietly reverses it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.modules.agent_surfaces.services.notification_rate_limiter import (
    EmailSendLimitExceeded,
    NotificationRateLimitExceeded,
    NotificationRateLimiter,
)

pytestmark = pytest.mark.asyncio


class FakeRedis:
    """A fixed-window counter, which is all the limiter uses."""

    def __init__(self, *, fail: bool = False):
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self._fail = fail

    async def incr(self, key: str) -> int:
        if self._fail:
            raise RedisError("connection refused")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expiries[key] = ttl


async def test_the_hourly_budget_is_per_pod_and_per_recipient():
    """Two pods messaging one person are two relationships.

    One pod's runaway loop must not mute the other, so the key carries both.
    """
    redis = FakeRedis()
    limiter = NotificationRateLimiter(limit=2, redis=redis)
    pod_a, pod_b, recipient = uuid4(), uuid4(), uuid4()

    await limiter.check(pod_id=pod_a, recipient_user_id=recipient)
    await limiter.check(pod_id=pod_a, recipient_user_id=recipient)
    # Pod B's first send, against the same person, is unaffected.
    await limiter.check(pod_id=pod_b, recipient_user_id=recipient)

    with pytest.raises(NotificationRateLimitExceeded):
        await limiter.check(pod_id=pod_a, recipient_user_id=recipient)

    assert redis.expiries[f"notify:rate:{pod_a}:{recipient}"] == 3600


async def test_the_email_budget_is_per_pod_and_counts_every_recipient():
    """The gap the per-recipient limit leaves wide open.

    An agent messaging five hundred different people once each never trips the
    hourly limit, and every one of those emails leaves the same shared sending
    domain. This is the limit that notices.
    """
    redis = FakeRedis()
    limiter = NotificationRateLimiter(email_limit=3, redis=redis)
    pod_id = uuid4()

    for _ in range(3):
        await limiter.check_email(pod_id=pod_id)

    with pytest.raises(EmailSendLimitExceeded) as exc:
        await limiter.check_email(pod_id=pod_id)

    assert "3 emails today" in str(exc.value)
    assert redis.expiries[f"notify:rate:email:{pod_id}"] == 86_400


async def test_the_two_budgets_do_not_spend_each_other():
    """Separate keys. A pod at its email ceiling can still reach Slack."""
    redis = FakeRedis()
    limiter = NotificationRateLimiter(limit=1, email_limit=1, redis=redis)
    pod_id, recipient = uuid4(), uuid4()

    await limiter.check_email(pod_id=pod_id)
    # The hourly per-recipient budget is untouched by that email.
    await limiter.check(pod_id=pod_id, recipient_user_id=recipient)

    with pytest.raises(EmailSendLimitExceeded):
        await limiter.check_email(pod_id=pod_id)


@pytest.mark.parametrize("call", ["check", "check_email"])
async def test_an_unreachable_redis_lets_the_message_through(call):
    """Fails open, deliberately, and only for Redis being unreachable.

    "Nobody can be told anything while Redis is down" is the wrong side to fail
    on for a system whose whole premise is that people get told. A bug in the
    limiter itself still raises — only ``RedisError``/``OSError`` is caught.
    """
    limiter = NotificationRateLimiter(
        limit=0, email_limit=0, redis=FakeRedis(fail=True)
    )

    if call == "check":
        await limiter.check(pod_id=uuid4(), recipient_user_id=uuid4())
    else:
        await limiter.check_email(pod_id=uuid4())
