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


async def test_process_cache_registry_closes_each_live_client_once(monkeypatch) -> None:
    first = RedisJsonCache[str]("redis://first", "first", 60)
    second = RedisJsonCache[str]("redis://second", "second", 60)
    first_client = _Redis()
    second_client = _Redis()
    clients = {
        "redis://first": first_client,
        "redis://second": second_client,
    }
    monkeypatch.setattr(
        redis_json_cache.Redis,
        "from_url",
        lambda url, **_kwargs: clients[url],
    )
    await first.set_raw("key", "value")
    await second.set_raw("key", "value")

    await close_redis_json_caches()
    await close_redis_json_caches()

    assert first_client.close_count == 1
    assert second_client.close_count == 1
