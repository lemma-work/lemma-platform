"""Public, hit-capped, short signed URLs for datastore files.

Unlike the stateless HMAC tokens in ``file_url.py``, these links are looked up
rather than decoded, so we can:

- keep them **short** (a random code, not an embedded payload) — easy for an
  agent to copy/paste and pass around, and
- enforce a **maximum hit count** per link so a leaked link can't run up
  unbounded egress on a large file.

Two stores, and which owns what is the whole design:

**Postgres owns whether the link exists**, where it points, and when it dies.
A link is a capability grant, not a cache — it is all that stands between a URL
and someone's file — and it now lasts up to seven days. Redis durability is a
property of the deployment rather than of this code (the compose stack snapshots
every 60s with no AOF; managed key-value services differ again), so a link that
lived only there survived or vanished according to how the operator had
deployed. That is not a lifetime anyone can promise a recipient. The row is also
what makes revocation possible at all: you cannot kill a link that exists only
as a key nobody has listed.

**Redis owns the spend counter**, and caches the claims so the common fetch
touches no database. The counter deliberately stays lossy. ``/s/`` is
unauthenticated and unrate-limited, so a database write per byte-serving fetch
would be an anonymous write lever and would serialize every reader of a popular
link on one row — worse since Range landed, because a video player issues dozens
of requests per view. And it fails the right way round: lose Redis and links
still resolve while budget accounting resets, where losing the record instead
would kill links that are perfectly valid. Expiry still bounds a link whose
counter was lost.

Resetting the budget that way is accepted, not tolerated. The cap exists to stop
a *leaked* link running up unbounded egress; it is not an accounting guarantee,
and nothing bills against it. Losing one link's worth of counting to a Redis
failover costs at most one link's worth of extra egress, which is well inside
what the cap was ever meant to prevent. Do not "fix" this by moving the counter
into Postgres — the paragraph above is why. If it ever needs to be tighter, the
move is a windowed budget (per hour, say, checkpointed durably), which bounds a
failover's loss to one window instead of the whole link and still keeps the hot
path in Redis.

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

from uuid import UUID, uuid7

from redis.asyncio import Redis

from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.redis.client import get_redis

from app.core.config import settings
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import DatastoreSignedLinkLimitError
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreSignedLinkEntity,
)
from app.modules.datastore.infrastructure.repositories.signed_link_repository import (
    SignedLinkRepository,
)
from app.modules.datastore.services.files.projection import datastore_storage_key
from app.core.log.log import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "datastore:signedurl"

# How long a revocation tombstone blocks the cache from being rewritten. Only
# has to outlive a rehydrate that is already in flight — a row read and a cache
# write — so this is generous by orders of magnitude.
_REVOKED_TOMBSTONE_SECONDS = 300

# Atomically charge ARGV[1] bytes against the link's budget and return its
# claims. Running it server-side keeps the existence check, the budget test and
# the charge a single atomic step.
#
# Returns {-1} when the key is missing or expired, {-2} when the budget is spent
# or this request would not fit inside what is left of it — testing the whole
# request rather than only the running total is what makes the budget a ceiling
# instead of a line one response is always allowed to cross, and otherwise a fixed-arity
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
if budget > 0 and (spent >= budget or spent + wanted > budget) then
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

    @staticmethod
    def _tombstone_key(code: str) -> str:
        return f"{_KEY_PREFIX}:revoked:{code}"

    async def create(
        self,
        *,
        file: DatastoreFileEntity,
        links: SignedLinkRepository,
        created_by_user_id: UUID | None = None,
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
        max_active = datastore_settings.datastore_signed_url_max_active_per_user

        code = secrets.token_urlsafe(datastore_settings.datastore_signed_url_code_bytes)
        expires_at = datetime.fromtimestamp(
            int(time.time()) + expires_seconds, tz=timezone.utc
        )

        # The durable record first, deliberately. If Redis then fails the link
        # still resolves — the next fetch rehydrates from this row. The other
        # order gives you a link that works until Redis forgets it, which is the
        # failure this table exists to remove.
        link = DatastoreSignedLinkEntity(
            id=uuid7(),
            code=code,
            pod_id=file.pod_id,
            created_by_user_id=created_by_user_id,
            path=file.path,
            object_key=object_key,
            content_type=file.content_type,
            filename=file.name,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            max_hits=max_hits,
            expires_at=expires_at,
        )
        # The caller's own unit of work, not a second one. Opening a session
        # here while the request already held one meant every concurrent mint
        # needed two pooled connections, and ten of them exhausted a pool of ten
        # and 500ed unrelated requests.
        #
        # One statement decides and writes; see `create_within_allowance` for
        # why neither a separate count nor a lock is used. Committed here rather
        # than at the end of the request, because the Redis entry written below
        # must not exist before the row it describes.
        inserted = await links.create_within_allowance(link, max_active=max_active)
        if not inserted:
            live = await links.count_live_for_user(file.pod_id, created_by_user_id)
            raise DatastoreSignedLinkLimitError(
                limit=max_active, live=max(live, max_active)
            )
        await links.commit()

        # Everything past the commit is Redis and string building, so the
        # pooled connection goes back before any of it. Holding one across
        # non-database work is what #717 spent twelve fixes removing, and a
        # burst of mints is exactly the shape that turns it into pool
        # exhaustion.
        async with connection_released(links.session):
            await self._cache(link)
            signed_url = f"{settings.api_url.rstrip('/')}/s/{code}"
        return code, signed_url, expires_at, max_hits

    async def _cache(self, link: DatastoreSignedLinkEntity) -> None:
        """Put a link's claims in Redis with the counter it will spend against.

        Called on mint and again whenever a fetch finds nothing cached, which is
        what makes a lost Redis a slow first request rather than a dead link.
        """
        ttl = int((link.expires_at - datetime.now(timezone.utc)).total_seconds())
        if ttl <= 0:
            return
        redis = await self._get_redis()
        if await redis.exists(self._tombstone_key(link.code)):
            # Revoked while this caller was mid-rehydrate. Writing now would
            # undo the revocation for the rest of the link's lifetime.
            return
        key = self._key(link.code)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "object_key": link.object_key,
                    "pod_id": str(link.pod_id),
                    "path": link.path,
                    "content_sha256": link.content_sha256 or "",
                    "content_type": link.content_type,
                    "filename": link.filename,
                    "size_bytes": link.size_bytes,
                    "max_hits": link.max_hits,
                    # A budget of 0 means "uncounted", which is what an unknown
                    # or zero size has to fall back to: multiplying it out would
                    # otherwise mint a link that is exhausted before its first
                    # fetch. Expiry still bounds such a link.
                    "budget_bytes": link.size_bytes * link.max_hits,
                    "spent_bytes": 0,
                },
            )
            pipe.expire(key, ttl)
            await pipe.execute()

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

        if not result or int(result[0]) == -1:
            # Nothing cached. Either this code never existed, or Redis lost it
            # while the link is still live — indistinguishable from here, so ask
            # the durable record and try once more. Note the budget starts over
            # when this happens; see the module docstring for why that is the
            # side to fail on.
            if not await self._rehydrate(code):
                raise SignedUrlNotFound(code)
            result = await redis.eval(_CONSUME_LUA, 1, key, max(0, bytes_wanted))

        if not result:
            raise SignedUrlNotFound(code)
        head = int(result[0])
        if head == -1:
            raise SignedUrlNotFound(code)
        if head == -2:
            # Durably first, then burn the cached copy. The other order — which
            # is what this was before the record existed — drops the key and
            # leaves a live row behind, so the very next fetch rehydrates, mints
            # a fresh budget and serves the file again. The cap has to be spent
            # somewhere that survives losing the counter.
            async with SessionUnitOfWorkFactory(get_session_maker())() as uow:
                await SignedLinkRepository(uow).mark_exhausted(code)
                await uow.commit()
            with suppress(Exception):
                await redis.delete(key)
            raise SignedUrlExhausted(code)

        return SignedUrlClaims(
            object_key=result[2],
            content_sha256=result[3] or None,
            content_type=result[4] or "application/octet-stream",
            filename=result[5] or result[2].rsplit("/", 1)[-1] or "file",
        )

    async def _rehydrate(self, code: str) -> bool:
        """Reload a link's claims into Redis from the durable record.

        Returns whether the link is live. The session is opened and closed here
        rather than handed in, because the caller is the public serving route:
        it goes on to stream a response body, and a pooled connection held
        across that is a connection held for as long as the download takes.
        """
        async with SessionUnitOfWorkFactory(get_session_maker())() as uow:
            link = await SignedLinkRepository(uow).get_by_code(code)
        if link is None or not link.is_live:
            return False
        await self._cache(link)
        # Deliberately no `code`: it is the whole capability, so a log line
        # carrying one hands a link to anyone who can read logs. The pod is
        # enough to tell whether rehydration is happening and for whom.
        logger.debug(
            "datastore.signed_url.rehydrated_link_from_record.observed",
            pod_id=str(link.pod_id),
        )
        return True

    async def revoke(
        self, pod_id: UUID, code: str, *, links: SignedLinkRepository
    ) -> bool:
        """Kill a link now, before it expires. Returns whether it was live.

        Both halves, and the cache first: while the row is what makes the link
        exist, the cache is what actually answers a fetch, so dropping it is
        what makes revocation take effect immediately. A failure between the two
        leaves the link dead in Redis and revocable again from the record.
        """
        # The record first. Deleting the cache first left a window where a
        # concurrent fetch rehydrated from a row that was still live and put the
        # entry straight back, so the link kept working until its TTL.
        revoked = await links.revoke(pod_id, code)
        await links.commit()

        # Committing first shrinks that window but does not close it: a
        # rehydrate that had already read the live row can still write the cache
        # after the delete below. The tombstone closes it — `_cache` refuses to
        # write while one exists, and it outlives any in-flight rehydrate.
        # Redis only from here, so the connection goes back first.
        async with connection_released(links.session):
            redis = await self._get_redis()
            with suppress(Exception):
                async with redis.pipeline(transaction=True) as pipe:
                    pipe.setex(self._tombstone_key(code), _REVOKED_TOMBSTONE_SECONDS, 1)
                    pipe.delete(self._key(code))
                    await pipe.execute()
        return revoked

    async def consume(self, code: str) -> str:
        """Compatibility wrapper returning only the object key."""
        return (await self.peek_claims(code)).object_key


_store: SignedUrlStore | None = None


def get_signed_url_store() -> SignedUrlStore:
    global _store
    if _store is None:
        _store = SignedUrlStore()
    return _store
