"""Public, hit-capped, short signed URLs for datastore files.

Unlike the stateless HMAC tokens in ``file_url.py``, these links are backed by
Redis so we can:

- keep them **short** (a random code, not an embedded payload) — easy for an
  agent to copy/paste and pass around, and
- enforce a **maximum hit count** per link so a leaked link can't run up
  unbounded egress on a large file.

The code *is* the capability: anyone holding ``{api_url}/s/{code}`` can fetch the
bytes (until the link expires or its budget is spent). Bytes are streamed
**through the backend** (``GET /s/{code}``) rather than redirecting to a real
object-store signed URL — that is the only way the cap actually bounds egress.

The cap is a **byte budget**, not a request count: ``max_hits`` whole copies of
the file. It reads as a download count to a caller, which is how it is named and
documented, but counting requests was wrong in both directions. A revalidation
(304) or a link-unfurling bot's HEAD transfers nothing and used to cost a whole
download — enough that a browser reloading a PDF killed its own link, since
``no-cache`` makes it revalidate every time. And a ranged request transfers a
slice, so a video player seeking through a file would have spent a download per
seek. Charging the bytes a response actually commits to gets both right and is
what "bounds egress" meant in the first place.
"""

from __future__ import annotations

from contextlib import suppress
import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from redis.asyncio import Redis

from app.core.infrastructure.redis.client import get_redis

from app.core.config import settings
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.files.projection import datastore_storage_key
from app.core.log.log import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "datastore:signedurl"

# Atomically charge ARGV[1] bytes against the link's budget and return its
# claims. Running it server-side keeps the existence check, the budget test and
# the charge a single atomic step.
#
# Returns {-1} when the key is missing or expired, {-2} when the budget is
# already spent, and otherwise a fixed-arity
# {spent, budget, object_key, content_sha256, content_type, filename}.
# Fixed arity matters: Redis truncates a returned Lua table at its first nil, so
# a hash missing one field used to hand Python a short list and raise IndexError
# — a 500 where the honest answer was 404. Every field is coerced here instead.
_CONSUME_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {-1}
end
local wanted = tonumber(ARGV[1]) or 0
local budget = tonumber(redis.call('HGET', KEYS[1], 'budget_bytes')) or 0
local spent = tonumber(redis.call('HGET', KEYS[1], 'spent_bytes')) or 0
if budget > 0 and spent >= budget then
  return {-2}
end
if wanted > 0 then
  spent = redis.call('HINCRBY', KEYS[1], 'spent_bytes', wanted)
end
return {
  spent,
  budget,
  redis.call('HGET', KEYS[1], 'object_key') or '',
  redis.call('HGET', KEYS[1], 'content_sha256') or '',
  redis.call('HGET', KEYS[1], 'content_type') or '',
  redis.call('HGET', KEYS[1], 'filename') or ''
}
"""


class SignedUrlNotFound(Exception):
    """The short code is unknown or has expired."""


class SignedUrlExhausted(Exception):
    """The short link has been fetched its maximum number of times."""


@dataclass(frozen=True, slots=True)
class SignedUrlClaims:
    object_key: str
    content_sha256: str | None
    content_type: str
    filename: str


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
                self._redis = get_redis(url=self._redis_url)
        return self._redis

    @staticmethod
    def _key(code: str) -> str:
        return f"{_KEY_PREFIX}:{code}"

    async def create(
        self,
        *,
        file: DatastoreFileEntity,
        expires_seconds: int | None = None,
        max_hits: int | None = None,
    ) -> tuple[str, str, datetime, int]:
        """Mint a short link. Returns ``(code, signed_url, expires_at, max_hits)``.

        ``expires_seconds`` and ``max_hits`` are clamped to the configured
        defaults/ceilings, so callers can pass user input directly.

        Takes the whole entity rather than a handful of fields because the
        serving route is public: it has no pod context to look anything up in,
        so everything it will need to answer with — type, name, size, digest —
        has to be copied into the record here. Reading them off the entity in
        one place is also what keeps an extensionless file served as what it
        actually is rather than as ``application/octet-stream``.
        """
        object_key = datastore_storage_key(file)
        content_sha256 = file.content_sha256
        size_bytes = max(0, file.size_bytes or 0)
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
                    "pod_id": str(file.pod_id),
                    "path": file.path,
                    "content_sha256": content_sha256 or "",
                    "content_type": file.content_type,
                    "filename": file.name,
                    "size_bytes": size_bytes,
                    "max_hits": max_hits,
                    # A budget of 0 means "uncounted", which is what an unknown
                    # or zero size has to fall back to: multiplying it out would
                    # otherwise mint a link that is exhausted before its first
                    # fetch. Expiry still bounds such a link.
                    "budget_bytes": size_bytes * max_hits,
                    "spent_bytes": 0,
                },
            )
            pipe.expire(key, expires_seconds)
            await pipe.execute()

        expires_at = datetime.fromtimestamp(
            int(time.time()) + expires_seconds, tz=timezone.utc
        )
        signed_url = f"{settings.api_url.rstrip('/')}/s/{code}"
        return code, signed_url, expires_at, max_hits

    async def peek_claims(self, code: str) -> SignedUrlClaims:
        """The link's claims, charging nothing.

        Used to decide what the response *would* be — its type, size and
        validity — before committing to any bytes. A request that then turns out
        to transfer nothing (a 304, a HEAD, an unsatisfiable range) or to fail
        (the object is gone) therefore costs the link nothing, where the old
        increment-first ordering spent a download on each of them.
        """
        return await self._consume(code, bytes_wanted=0)

    async def consume_claims(
        self, code: str, *, bytes_wanted: int = 0
    ) -> SignedUrlClaims:
        """Charge ``bytes_wanted`` against the link's budget, returning its claims.

        Raises ``SignedUrlNotFound`` when the code is unknown or expired, and
        ``SignedUrlExhausted`` once the budget is spent.
        """
        return await self._consume(code, bytes_wanted=bytes_wanted)

    async def _consume(self, code: str, *, bytes_wanted: int) -> SignedUrlClaims:
        redis = await self._get_redis()
        key = self._key(code)
        result = await redis.eval(_CONSUME_LUA, 1, key, max(0, bytes_wanted))

        if not result:
            raise SignedUrlNotFound(code)
        head = int(result[0])
        if head == -1:
            raise SignedUrlNotFound(code)
        if head == -2:
            # Burn the link so further attempts short-circuit as not-found
            # rather than repeatedly re-reporting a spent budget.
            with suppress(Exception):
                await redis.delete(key)
            raise SignedUrlExhausted(code)

        return SignedUrlClaims(
            object_key=result[2],
            content_sha256=result[3] or None,
            content_type=result[4] or "application/octet-stream",
            filename=result[5] or result[2].rsplit("/", 1)[-1] or "file",
        )

    async def consume(self, code: str) -> str:
        """Compatibility wrapper returning only the object key."""
        return (await self.peek_claims(code)).object_key


_store: SignedUrlStore | None = None


def get_signed_url_store() -> SignedUrlStore:
    global _store
    if _store is None:
        _store = SignedUrlStore()
    return _store
