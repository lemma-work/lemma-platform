"""Per-user daily rate limiter semantics with a fake Redis (no real Redis)."""

from __future__ import annotations

import pytest

from app.modules.pod_bundle.domain.errors import BundleRateLimitExceededError
from app.modules.pod_bundle.infrastructure.rate_limiter import BundleRateLimiter


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl


class _BrokenRedis:
    async def incr(self, key: str) -> int:
        raise RuntimeError("redis down")

    async def expire(self, key: str, ttl: int) -> None:  # pragma: no cover
        raise RuntimeError("redis down")


def _limiter(redis) -> BundleRateLimiter:
    limiter = BundleRateLimiter()
    limiter._redis = redis  # bypass Redis.from_url
    return limiter


async def test_allows_up_to_limit_then_raises_429():
    limiter = _limiter(_FakeRedis())
    for _ in range(5):
        await limiter.check_and_increment(user_id="u1", operation="export", limit=5)
    with pytest.raises(BundleRateLimitExceededError) as exc:
        await limiter.check_and_increment(user_id="u1", operation="export", limit=5)
    assert exc.value.status_code == 429


async def test_export_and_import_buckets_are_independent():
    redis = _FakeRedis()
    limiter = _limiter(redis)
    for _ in range(5):
        await limiter.check_and_increment(user_id="u1", operation="export", limit=5)
    # Import bucket is untouched by a day of exports.
    await limiter.check_and_increment(user_id="u1", operation="import", limit=5)


async def test_users_do_not_share_a_bucket():
    limiter = _limiter(_FakeRedis())
    for _ in range(5):
        await limiter.check_and_increment(user_id="u1", operation="export", limit=5)
    # A different user starts fresh.
    await limiter.check_and_increment(user_id="u2", operation="export", limit=5)


async def test_zero_limit_disables_the_cap():
    limiter = _limiter(_FakeRedis())
    for _ in range(50):
        await limiter.check_and_increment(user_id="u1", operation="export", limit=0)


async def test_ttl_is_set_only_on_first_increment():
    redis = _FakeRedis()
    limiter = _limiter(redis)
    await limiter.check_and_increment(user_id="u1", operation="export", limit=5)
    assert len(redis.expires) == 1
    await limiter.check_and_increment(user_id="u1", operation="export", limit=5)
    assert len(redis.expires) == 1  # not refreshed on every call


async def test_fails_open_when_redis_errors():
    limiter = _limiter(_BrokenRedis())
    # Well past any limit, but a Redis blip must never block a legitimate job.
    await limiter.check_and_increment(user_id="u1", operation="export", limit=1)
    await limiter.check_and_increment(user_id="u1", operation="export", limit=1)


class _IncrementsButCannotExpire:
    """Redis answers INCR and then fails EXPIRE — a partial outage, not a blip."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        raise RuntimeError("redis read-only")


async def test_a_counted_start_is_enforced_even_when_the_ttl_cannot_be_set():
    """Setting the TTL shared a try block with the INCR that answers the guard.

    A failed EXPIRE therefore returned "under the limit" from a call that had
    already counted the start — so the one path where the counter is known to
    work was the path that skipped the check. The key is date-stamped, so a
    missing TTL only leaves it to linger; it is never read on another day."""
    limiter = _limiter(_IncrementsButCannotExpire())

    await limiter.check_and_increment(user_id="u1", operation="import", limit=1)
    with pytest.raises(BundleRateLimitExceededError):
        await limiter.check_and_increment(user_id="u1", operation="import", limit=1)
