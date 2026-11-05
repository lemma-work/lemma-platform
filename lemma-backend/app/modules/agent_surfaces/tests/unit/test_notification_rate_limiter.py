"""Bounding how much mail an agent can send from the domain every pod shares.

The email budget never refuses the line itself -- it is in the member's
channel whatever it says. The Redis-is-down behaviour is covered too, because
"fails open" is a decision that should break a test if someone quietly
reverses it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.modules.agent_surfaces.services.notification_rate_limiter import (
    EmailBudget,
    EmailSendLimitExceeded,
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


async def test_the_email_budget_is_per_pod_and_counts_every_recipient():
    """An agent messaging five hundred different people once each is one
    push window each, and every one of those emails leaves the same shared
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
