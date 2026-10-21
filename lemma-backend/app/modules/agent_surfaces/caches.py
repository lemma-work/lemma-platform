"""Every Redis cache this module keeps, built in one place.

Each store here used to build its own `RedisJsonCache` behind a module global,
and noticed a changed Redis URL by reading the cache's private `_redis_url` --
the same dozen lines a dozen times over, and a test that wanted its own cache
had to know which global to overwrite. They are namespaces of one thing, so
they are declared together: this file is also the list of every keyspace the
module writes, and how long each one lives.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache


@dataclass(frozen=True, slots=True)
class CacheNamespace:
    key_prefix: str
    ttl_seconds: int


_DAY = 24 * 60 * 60

DECISION_CARDS = CacheNamespace("surface:decision-card", 30 * _DAY)
#: A run's live Slack stream, so its reply can finish that message in place.
#: Outlives the longest reply retry (30 min) with room to spare.
LIVE_DISPLAYS = CacheNamespace("surface:live-display", _DAY)
TELEGRAM_CALLBACK_TOKENS = CacheNamespace("surface:telegram-cb", 7 * _DAY)
TELEGRAM_BOT_INFO = CacheNamespace("surface:telegram-bot", 3600)
TEAMS_TOKENS = CacheNamespace("surface:teams-token", 3600)
TEAMS_OIDC = CacheNamespace("surface:oidc", 10 * 60)
TEAMS_CONSENT_NONCES = CacheNamespace("surface:teams-consent-nonce", 3600)
TEAMS_CONSENT_CHECKS = CacheNamespace("surface:teams-consent", 60)
PENDING_DISPLAY_PATHS = CacheNamespace("surface:pending-display-paths", 6 * 60 * 60)
INGESTED_ATTACHMENTS = CacheNamespace("surface:ingested", _DAY)
WEB_WIDGET_KEYS = CacheNamespace("web-widget:key", 60)
#: Whether a Slack or Teams place is private, as the platform said when its
#: mirror was about to be made. Short: a conversion to private inside it is
#: still caught by the next message, which names the new type.
PLACE_PRIVACY = CacheNamespace("surface:place-privacy", 5 * 60)


class SurfaceCaches:
    """One `RedisJsonCache` per namespace, for the Redis the process is using.

    The URL is read on every request for a cache rather than captured once,
    because a test that points `settings.redis_url` somewhere else must not be
    served a cache bound to the old one.
    """

    def __init__(self, *, redis_url: Callable[[], str] | None = None) -> None:
        self._redis_url = redis_url or (lambda: settings.redis_url)
        self._built: dict[CacheNamespace, tuple[str, RedisJsonCache]] = {}
        self._substitutes: dict[CacheNamespace, RedisJsonCache] = {}

    def get(self, namespace: CacheNamespace) -> RedisJsonCache:
        substitute = self._substitutes.get(namespace)
        if substitute is not None:
            return substitute
        url = self._redis_url()
        built = self._built.get(namespace)
        if built is not None and built[0] == url:
            return built[1]
        cache = RedisJsonCache(
            redis_url=url,
            key_prefix=namespace.key_prefix,
            ttl_seconds=namespace.ttl_seconds,
        )
        self._built[namespace] = (url, cache)
        return cache

    @contextmanager
    def substituted(
        self, namespace: CacheNamespace, cache: RedisJsonCache
    ) -> Iterator[RedisJsonCache]:
        """Serve ``cache`` for ``namespace`` inside the block.

        For a caller -- a test, a second process stood in by a second cache --
        that needs its own store under a namespace without knowing which module
        reads it.
        """
        previous = self._substitutes.get(namespace)
        self._substitutes[namespace] = cache
        try:
            yield cache
        finally:
            if previous is None:
                self._substitutes.pop(namespace, None)
            else:
                self._substitutes[namespace] = previous


surface_caches = SurfaceCaches()
