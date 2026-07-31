from app.core.infrastructure.cache import redis_json_cache
from app.core.infrastructure.cache.redis_json_cache import (
    RedisJsonCache,
    close_redis_json_caches,
)


class _Redis:
    def __init__(self) -> None:
        self.close_count = 0

    async def aclose(self) -> None:
        self.close_count += 1

    async def set(self, *_args, **_kwargs) -> None:
        return None


async def test_caches_reuse_the_shared_client_per_url(monkeypatch) -> None:
    """Two caches on one URL must not open two pools."""
    clients: dict[str, _Redis] = {}

    def fake_get_redis(*, url=None, decode_responses=True):
        return clients.setdefault(url, _Redis())

    monkeypatch.setattr(redis_json_cache, "get_redis", fake_get_redis)

    first = RedisJsonCache[str]("redis://shared", "first", 60)
    second = RedisJsonCache[str]("redis://shared", "second", 60)
    await first.set_raw("key", "value")
    await second.set_raw("key", "value")

    assert len(clients) == 1
    assert await first._get_redis() is await second._get_redis()


async def test_teardown_releases_clients_without_closing_the_shared_pool(
    monkeypatch,
) -> None:
    """A cache borrows the process-wide client; it must not close it.

    Closing here would break every other component still holding the same
    pool. Disposing of it is close_redis_clients()'s job.
    """
    client = _Redis()

    def fake_get_redis(*, url=None, decode_responses=True):
        return client

    monkeypatch.setattr(redis_json_cache, "get_redis", fake_get_redis)

    cache = RedisJsonCache[str]("redis://first", "first", 60)
    await cache.set_raw("key", "value")
    assert cache._redis is not None

    await close_redis_json_caches()
    await close_redis_json_caches()

    assert client.close_count == 0
    assert cache._redis is None
