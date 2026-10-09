"""The per-organization window, and which provider a deployment gets."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError

from app.modules.decisions.config import DecisionsSettings
from app.modules.decisions.infrastructure.providers.registry import build_provider
from app.modules.decisions.infrastructure.rate_limit import OrganizationRateLimiter

pytestmark = pytest.mark.unit


class _Counter:
    """Redis's INCR-with-TTL script, in memory."""

    def __init__(self, *, fails: BaseException | None = None, delay: float = 0) -> None:
        self.counts: dict[str, int] = {}
        self.fails = fails
        self.delay = delay

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fails is not None:
            raise self.fails
        key = str(keys_and_args[0])
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


async def test_the_limit_resets_with_the_clock_minute() -> None:
    counter = _Counter()
    limiter = OrganizationRateLimiter(
        limit_per_minute=2, redis=counter, clock=lambda: 120.0 + 45.5
    )

    assert await limiter.retry_after("org:a") is None
    assert await limiter.retry_after("org:a") is None
    assert await limiter.retry_after("org:a") == 15  # until 180.0
    assert await limiter.retry_after("org:b") is None


async def test_no_limit_means_no_redis_call() -> None:
    counter = _Counter()

    assert (
        await OrganizationRateLimiter(limit_per_minute=0, redis=counter).retry_after(
            "org:a"
        )
        is None
    )
    assert counter.counts == {}


@pytest.mark.parametrize(
    "counter",
    [_Counter(fails=RedisConnectionError("down")), _Counter(delay=1.0)],
    ids=["redis-down", "redis-slow"],
)
async def test_redis_trouble_lets_the_decision_through(counter: _Counter) -> None:
    limiter = OrganizationRateLimiter(limit_per_minute=1, redis=counter)

    assert await limiter.retry_after("org:a") is None


def test_the_model_provider_is_the_default() -> None:
    assert build_provider(DecisionsSettings()).name == "model"


def test_typesafe_is_chosen_by_setting_when_its_key_is_set() -> None:
    provider = build_provider(
        DecisionsSettings(
            decision_provider="typesafe", typesafe_api_key=SecretStr("sk-test")
        )
    )
    assert provider.name == "typesafe"


def test_typesafe_without_its_key_falls_back_to_the_model() -> None:
    """Decisions keep working on the deployment's model rather than failing
    every call because one key is missing."""
    provider = build_provider(
        DecisionsSettings(decision_provider="typesafe", decision_model="fast-model")
    )
    assert provider.name == "model"
    assert provider._model_name == "fast-model"
