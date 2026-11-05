"""Bounding what an agent can send, in the two ways it can run away.

The push budget bounds how often one pod puts a line on one person's phone; the
email budget bounds how much one pod spends of the sending domain every pod
shares. Neither refuses the line itself -- it is in the member's channel
whatever they say. Both windows are covered here, including the Redis-is-down
behaviour, because "fails open" is a decision that should break a test if
someone quietly reverses it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.modules.agent_surfaces.services.notification_rate_limiter import (
    EmailBudget,
    EmailSendLimitExceeded,
    PushBudget,
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

    async def eval(self, script, numkeys, key, ttl):
        """``incr_with_ttl``'s script: increment, and expire if not expiring."""
        count = await self.incr(key)
        if key not in self.expiries:
            await self.expire(key, ttl)
        return count


async def test_the_push_budget_is_per_pod_and_per_recipient():
    """Two pods messaging one person are two relationships.

    One pod's runaway loop must not mute the other, so the key carries both.
    """
    redis = FakeRedis()
    budget = PushBudget(limit=2, redis=redis)
    pod_a, pod_b, recipient = uuid4(), uuid4(), uuid4()

    assert await budget.allows(pod_id=pod_a, recipient_user_id=recipient)
    assert await budget.allows(pod_id=pod_a, recipient_user_id=recipient)
    # Pod B's first push, to the same person, is unaffected.
    assert await budget.allows(pod_id=pod_b, recipient_user_id=recipient)

    assert await budget.allows(pod_id=pod_a, recipient_user_id=recipient) is False
    assert redis.expiries[f"notify:rate:{pod_a}:{recipient}"] == 3600


async def test_the_email_budget_is_per_pod_and_counts_every_recipient():
    """The gap the per-recipient budget leaves wide open.

    An agent messaging five hundred different people once each never trips the
    hourly push budget, and every one of those emails leaves the same shared
    sending domain. This is the budget that notices.
    """
    redis = FakeRedis()
    budget = EmailBudget(limit=3, redis=redis)
    pod_id = uuid4()

    for _ in range(3):
        await budget.check(pod_id=pod_id)

    with pytest.raises(EmailSendLimitExceeded) as exc:
        await budget.check(pod_id=pod_id)

    assert "3 emails today" in str(exc.value)
    assert redis.expiries[f"notify:rate:email:{pod_id}"] == 86_400


async def test_the_two_budgets_do_not_spend_each_other():
    """Separate keys. A pod at its email ceiling can still reach Slack."""
    redis = FakeRedis()
    email, push = EmailBudget(limit=1, redis=redis), PushBudget(limit=1, redis=redis)
    pod_id, recipient = uuid4(), uuid4()

    await email.check(pod_id=pod_id)
    # The hourly per-recipient budget is untouched by that email.
    assert await push.allows(pod_id=pod_id, recipient_user_id=recipient)

    with pytest.raises(EmailSendLimitExceeded):
        await email.check(pod_id=pod_id)


async def test_an_unreachable_redis_lets_the_email_through():
    """Fails open, deliberately, and only for Redis being unreachable.

    "Nobody can be told anything while Redis is down" is the wrong side to fail
    on for a system whose whole premise is that people get told. A bug in the
    budget itself still raises — only ``RedisError``/``OSError`` is caught.
    """
    # A limit of zero refuses every email whenever Redis can answer, so
    # returning at all is the fail-open path being taken.
    assert (
        await EmailBudget(limit=0, redis=FakeRedis(fail=True)).check(pod_id=uuid4())
        is None
    )


async def test_an_unreachable_redis_lets_the_push_through():
    """The same decision for the push budget: a zero limit, and still allowed."""
    budget = PushBudget(limit=0, redis=FakeRedis(fail=True))

    assert await budget.allows(pod_id=uuid4(), recipient_user_id=uuid4()) is True
