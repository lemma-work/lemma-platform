"""Public, hit-capped, short signed URLs for datastore files.

Unlike the stateless HMAC tokens in ``file_url.py``, these links are backed by
Redis so we can:

- keep them **short** (a random code, not an embedded payload) — easy for an
  agent to copy/paste and pass around, and
- enforce a **maximum hit count** per link so a leaked link can't run up
  unbounded egress on a large file.

The code *is* the capability: anyone holding ``{api_url}/s/{code}`` can fetch the
bytes (until the link expires or its hit cap is reached). Bytes are streamed
**through the backend** (``GET /s/{code}``) rather than redirecting to a real
object-store signed URL — that is the only way the hit cap actually bounds
egress.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.modules.datastore.config import datastore_settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "datastore:signedurl"

# Atomically record one hit. Returns {-1} when the key is missing/expired,
# otherwise {hits, max_hits, object_key}. Running it server-side keeps the
# existence check and increment a single atomic step.
_CONSUME_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {-1}
end
local hits = redis.call('HINCRBY', KEYS[1], 'hits', 1)
local max_hits = tonumber(redis.call('HGET', KEYS[1], 'max_hits'))
local object_key = redis.call('HGET', KEYS[1], 'object_key')
local content_sha256 = redis.call('HGET', KEYS[1], 'content_sha256') or ''
return {hits, max_hits, object_key, content_sha256}
"""


class SignedUrlNotFound(Exception):
    """The short code is unknown or has expired."""


class SignedUrlExhausted(Exception):
    """The short link has been fetched its maximum number of times."""


@dataclass(frozen=True, slots=True)
class SignedUrlClaims:
    object_key: str
    content_sha256: str | None


def _clamp(value: int | None, *, default: int, ceiling: int) -> int:
    if value is None:
        value = default
    return min(max(1, value), ceiling)


class SignedUrlStore:
    """Redis-backed store for hit-capped datastore file share links."""

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url or settings.redis_url
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _key(code: str) -> str:
        return f"{_KEY_PREFIX}:{code}"

    async def create(
        self,
        *,
        object_key: str,
        pod_id: UUID,
        path: str,
        content_sha256: str | None = None,
        expires_seconds: int | None = None,
        max_hits: int | None = None,
    ) -> tuple[str, str, datetime, int]:
        """Mint a short link. Returns ``(code, signed_url, expires_at, max_hits)``.

        ``expires_seconds`` and ``max_hits`` are clamped to the configured
        defaults/ceilings, so callers can pass user input directly.
        """
        expires_seconds = _clamp(
            expires_seconds,
            default=datastore_settings.datastore_signed_url_default_expiry_seconds,
            ceiling=datastore_settings.datastore_signed_url_max_expiry_seconds,
        )
        max_hits = _clamp(
            max_hits,
            default=datastore_settings.datastore_signed_url_default_max_hits,
            ceiling=datastore_settings.datastore_signed_url_max_hits,
        )

        code = secrets.token_urlsafe(datastore_settings.datastore_signed_url_code_bytes)
        redis = await self._get_redis()
        key = self._key(code)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "object_key": object_key,
                    "pod_id": str(pod_id),
                    "path": path,
                    "content_sha256": content_sha256 or "",
                    "max_hits": max_hits,
                    "hits": 0,
                },
            )
            pipe.expire(key, expires_seconds)
            await pipe.execute()

        expires_at = datetime.fromtimestamp(
            int(time.time()) + expires_seconds, tz=timezone.utc
        )
        signed_url = f"{settings.api_url.rstrip('/')}/s/{code}"
        return code, signed_url, expires_at, max_hits

    async def consume_claims(self, code: str) -> SignedUrlClaims:
        """Record a hit and return the stored download claims, or raise.

        Raises ``SignedUrlNotFound`` when the code is unknown/expired and
        ``SignedUrlExhausted`` once the per-link hit cap has been exceeded.
        """
        redis = await self._get_redis()
        key = self._key(code)
        result = await redis.eval(_CONSUME_LUA, 1, key)

        if not result or int(result[0]) == -1:
            raise SignedUrlNotFound(code)

        hits = int(result[0])
        max_hits = int(result[1])
        object_key = result[2]
        content_sha256 = result[3] or None
        if hits > max_hits:
            # Burn the link so further attempts short-circuit as not-found.
            try:
                await redis.delete(key)
            except Exception:  # best-effort cleanup
                logger.debug("datastore.signed_url.signed_url_cleanup_s.observed")
            raise SignedUrlExhausted(code)

        return SignedUrlClaims(
            object_key=object_key,
            content_sha256=content_sha256,
        )

    async def consume(self, code: str) -> str:
        """Compatibility wrapper returning only the object key."""
        return (await self.consume_claims(code)).object_key


_store: SignedUrlStore | None = None


def get_signed_url_store() -> SignedUrlStore:
    global _store
    if _store is None:
        _store = SignedUrlStore()
    return _store
