"""The one place this module's Redis caches are built."""

from __future__ import annotations

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.caches import CacheNamespace, SurfaceCaches

_NS = CacheNamespace("test:namespace", 30)


def test_one_cache_per_namespace_for_the_redis_in_use():
    url = ["redis://one"]
    caches = SurfaceCaches(redis_url=lambda: url[0])

    first = caches.get(_NS)
    assert caches.get(_NS) is first
    assert first.build_key("k") == "test:namespace:k"

    url[0] = "redis://two"
    assert caches.get(_NS) is not first, "a changed URL is not served the old one"


def test_a_substitute_is_served_inside_its_block_and_only_there():
    caches = SurfaceCaches(redis_url=lambda: "redis://one")
    built = caches.get(_NS)
    stand_in = RedisJsonCache(redis_url="redis://x", key_prefix="other", ttl_seconds=1)

    with caches.substituted(_NS, stand_in):
        assert caches.get(_NS) is stand_in
    assert caches.get(_NS) is built
