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


def _cache_over_fakeredis() -> RedisJsonCache[str]:
    import fakeredis

    cache = RedisJsonCache[str]("redis://unused", "ordered", 60)
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return cache


async def test_an_ordered_set_keeps_insertion_order_and_ignores_repeats() -> None:
    cache = _cache_over_fakeredis()

    for member in ("c", "a", "b", "a"):
        assert await cache.ordered_set_add("k", member, limit=10)

    assert await cache.ordered_set_members("k") == ["c", "a", "b"]


async def test_an_ordered_set_refuses_what_does_not_fit_and_keeps_what_does() -> None:
    cache = _cache_over_fakeredis()

    results = [await cache.ordered_set_add("k", str(i), limit=3) for i in range(5)]

    assert results == [True, True, True, False, False]
    assert await cache.ordered_set_members("k") == ["0", "1", "2"]
    # A member already held still counts as taken when the set is full.
    assert await cache.ordered_set_add("k", "1", limit=3)


async def test_concurrent_adds_to_an_ordered_set_all_land_exactly_once() -> None:
    import asyncio

    cache = _cache_over_fakeredis()

    results = await asyncio.gather(
        *(cache.ordered_set_add("k", str(i % 12), limit=50) for i in range(48))
    )

    assert all(results)
    assert sorted(await cache.ordered_set_members("k")) == sorted(
        str(i) for i in range(12)
    )


async def test_removing_from_an_ordered_set_leaves_later_additions() -> None:
    cache = _cache_over_fakeredis()
    await cache.ordered_set_add("k", "a", limit=5)
    await cache.ordered_set_add("k", "b", limit=5)

    await cache.ordered_set_remove("k", ["a"])
    await cache.ordered_set_add("k", "c", limit=5)

    assert await cache.ordered_set_members("k") == ["b", "c"]
    await cache.ordered_set_clear("k")
    assert await cache.ordered_set_members("k") == []
