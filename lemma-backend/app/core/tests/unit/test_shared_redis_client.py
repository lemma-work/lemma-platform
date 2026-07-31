from app.core.config import settings
from app.core.infrastructure.redis.client import (
    close_redis_clients,
    get_redis,
)


def test_identical_settings_share_one_pool() -> None:
    """The whole point: N components, one pool."""
    assert get_redis() is get_redis()
    assert get_redis(url="redis://example") is get_redis(url="redis://example")


def test_decode_responses_gets_its_own_pool() -> None:
    """FastStream and streaq need raw bytes; application code needs str.

    A single pool cannot serve both, so the flag participates in identity.
    """
    text_client = get_redis(decode_responses=True)
    binary_client = get_redis(decode_responses=False)
    assert text_client is not binary_client


def test_distinct_urls_get_distinct_pools() -> None:
    assert get_redis(url="redis://a") is not get_redis(url="redis://b")


def test_pool_is_bounded() -> None:
    """An unbounded pool exhausts the server's connection limit rather than
    applying backpressure, which is what redis-py's 2**31 default does."""
    client = get_redis(url="redis://bounded-check")
    assert client.connection_pool.max_connections == settings.redis_max_connections


def test_connections_are_health_checked() -> None:
    """Without this, a connection the server dropped while idle surfaces as an
    error in the middle of the next command."""
    client = get_redis(url="redis://health-check")
    assert client.connection_pool.connection_kwargs["health_check_interval"] == 30


async def test_close_is_idempotent_and_rebuilds_on_next_use() -> None:
    first = get_redis(url="redis://rebuild-check")
    await close_redis_clients()
    await close_redis_clients()

    second = get_redis(url="redis://rebuild-check")
    assert second is not first
