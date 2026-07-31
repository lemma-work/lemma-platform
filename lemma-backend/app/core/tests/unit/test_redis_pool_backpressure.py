"""Bounding a pool is not the same as applying backpressure.

redis-py's plain ``ConnectionPool`` raises ``ConnectionError("Too many
connections")`` the instant the ceiling is reached, so a bounded pool under a
burst fails commands rather than queueing them. Only ``BlockingConnectionPool``
waits. Blocking reads and Pub/Sub each hold a connection for their whole
duration here, which is exactly the shape that produces those bursts.
"""

from __future__ import annotations

from redis.asyncio import BlockingConnectionPool

from app.core.infrastructure.redis.client import get_redis


def test_the_pool_queues_rather_than_failing_a_burst() -> None:
    client = get_redis(url="redis://backpressure-check")

    assert isinstance(client.connection_pool, BlockingConnectionPool)
    assert client.connection_pool.timeout > 0


def test_connecting_cannot_hang_forever() -> None:
    """A Redis that accepts nothing must surface as an error, not a stall."""
    client = get_redis(url="redis://connect-timeout-check")

    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] > 0


def test_read_timeouts_are_opt_in_because_pubsub_blocks_forever() -> None:
    """``listen()`` performs an indefinite read; a default socket_timeout would
    tear the realtime multiplexer down and resubscribe on every idle interval."""
    default = get_redis(url="redis://read-timeout-check")

    assert default.connection_pool.connection_kwargs.get("socket_timeout") is None


def test_a_caller_asking_for_a_read_timeout_gets_its_own_pool() -> None:
    """Sharing one would impose that caller's timeout on Pub/Sub."""
    default = get_redis(url="redis://own-pool-check")
    bounded = get_redis(url="redis://own-pool-check", socket_timeout=15.0)

    assert bounded is not default
    assert bounded.connection_pool.connection_kwargs["socket_timeout"] == 15.0
    assert bounded is get_redis(url="redis://own-pool-check", socket_timeout=15.0)


def test_the_client_owns_its_pool_so_shutdown_actually_closes_it() -> None:
    """``Redis(connection_pool=...)`` does not take ownership; ``from_pool``
    does, and close_redis_clients relies on that to disconnect."""
    client = get_redis(url="redis://ownership-check")

    assert client.auto_close_connection_pool is True
