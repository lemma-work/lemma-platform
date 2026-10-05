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

The cap counts **whole copies of the file as it is now**, not requests: each
response is charged the fraction of the current file it commits to sending, in
millionths of an open. Counting requests was wrong in both directions. A
revalidation (304) or a link-unfurling bot's HEAD transfers nothing and used to
cost a whole download — enough that a browser reloading a PDF killed its own
link, since ``no-cache`` makes it revalidate every time. And a ranged request
transfers a slice, so a video player seeking through a file would have spent a
download per seek. It is a fraction rather than a byte total because the link is
**live**: it serves whatever is at the path when it is opened, so a page that
doubles in size after it was shared must not quietly halve the opens it has
left.

A shared page carries what it embeds — the images, stylesheets and scripts it
loads (``embedded_references``) — and those are charged in bytes against a
separate allowance that scales with the same ``max_hits``. Separate because a
reader opening a page with eight pictures has opened it once, not nine times;
counted at all because a leaked link must still bound egress through them.
"""

from __future__ import annotations

from contextlib import suppress
import asyncio
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from uuid import UUID, uuid7

from redis.asyncio import Redis
from redis.exceptions import RedisError

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
from app.modules.datastore.services.files.link_budget import (
    EMBEDDED_BYTES_PER_OPEN,
    OPEN_UNITS,
)
from app.modules.datastore.services.files.projection import datastore_storage_key
from app.core.log.log import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "datastore:signedurl"

# How long a revocation tombstone blocks the cache from being rewritten. Only
# has to outlive a rehydrate that is already in flight — a row read and a cache
# write — so this is generous by orders of magnitude.
_REVOKED_TOMBSTONE_SECONDS = 300

# Atomically charge ARGV[1] open-units against the link's budget and ARGV[2]
# bytes against its embedded allowance, and return its claims, as one step.
#
# {-1}: missing, expired, or cached before links went live (no `budget_units`;
# dropped so the record rebuilds it). {-2}: the budget is spent or this request
# would not fit in what is left — the whole request, which is what makes the
# budget a ceiling rather than a line one response may cross. {-3}: only the
# embedded allowance would be crossed. Otherwise a fixed-arity {exhausted,
# pod_id, path, created_by_user_id, minted_by_workload, content_type, filename}.
# Fixed arity matters: Redis truncates a returned Lua table at its first nil, so
# a hash missing one field used to hand Python a short list and raise IndexError
# — a 500 where the honest answer was 404. Every field is coerced here instead.
_CONSUME_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {-1}
end
if redis.call('HEXISTS', KEYS[1], 'budget_units') == 0 then
  redis.call('DEL', KEYS[1])
  return {-1}
end
local wanted = tonumber(ARGV[1]) or 0
local budget = tonumber(redis.call('HGET', KEYS[1], 'budget_units')) or 0
local spent = tonumber(redis.call('HGET', KEYS[1], 'spent_units')) or 0
if budget > 0 and (spent >= budget or spent + wanted > budget) then
  return {-2}
end
local embedded = tonumber(ARGV[2]) or 0
if embedded > 0 then
  local allowance = tonumber(redis.call('HGET', KEYS[1], 'embedded_budget_bytes')) or 0
  local used = tonumber(redis.call('HGET', KEYS[1], 'embedded_spent_bytes')) or 0
  if used + embedded > allowance then
    return {-3}
  end
  redis.call('HINCRBY', KEYS[1], 'embedded_spent_bytes', embedded)
end
local exhausted = 0
if wanted > 0 then
  spent = redis.call('HINCRBY', KEYS[1], 'spent_units', wanted)
end
if budget > 0 and spent >= budget then
  exhausted = 1
end
return {
  exhausted,
  redis.call('HGET', KEYS[1], 'pod_id') or '',
  redis.call('HGET', KEYS[1], 'path') or '',
  redis.call('HGET', KEYS[1], 'created_by_user_id') or '',
  redis.call('HGET', KEYS[1], 'minted_by_workload') or '',
  redis.call('HGET', KEYS[1], 'content_type') or '',
  redis.call('HGET', KEYS[1], 'filename') or ''
}
"""


# Write a link's claims, unless it has been revoked. KEYS[1] is the entry and
# KEYS[2] the revocation tombstone; both are examined and written in one step so
# a revoke cannot land between the check and the write.
_CACHE_LUA = """
if redis.call('EXISTS', KEYS[2]) == 1 then
  return 0
end
redis.call('DEL', KEYS[1])
redis.call('HSET', KEYS[1],
  'pod_id', ARGV[1],
  'path', ARGV[2],
  'created_by_user_id', ARGV[3],
  'minted_by_workload', ARGV[4],
  'content_type', ARGV[5],
  'filename', ARGV[6],
  'max_hits', ARGV[7],
  'budget_units', ARGV[8],
  'spent_units', 0,
  'embedded_budget_bytes', ARGV[9],
  'embedded_spent_bytes', 0
)
redis.call('PEXPIREAT', KEYS[1], ARGV[10])
return 1
"""


class SignedUrlNotFound(Exception):
    """The short code is unknown or has expired."""


class SignedUrlRevocationIncomplete(Exception):
    """The record says revoked, but the cached copy could not be dropped.

    Raised rather than swallowed because the link is still openable until the
    cache entry expires, so the caller has not got what it asked for. The
    revocation itself is durable; repeating the call finishes it.
    """


class SignedUrlExhausted(Exception):
    """The short link has been fetched its maximum number of times."""


class SignedUrlEmbeddedAllowanceSpent(Exception):
    """What the page embeds has used up its allowance; the page itself has not."""


@dataclass(frozen=True, slots=True)
class SignedUrlClaims:
    """Where a link points and on whose authority — never what is there now."""

    pod_id: UUID
    path: str
    created_by_user_id: UUID | None
    minted_by_workload: str | None
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
        minted_by_workload: str | None = None,
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
        # To the microsecond, not whole seconds. Truncating `now` took up to a
        # second off every link, so one minted with `expires_seconds=1` could
        # have milliseconds to live and be 404 by the time its URL was fetched.
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)

        # The durable record first, deliberately. If Redis then fails the link
        # still resolves — the next fetch rehydrates from this row. The other
        # order gives you a link that works until Redis forgets it, which is the
        # failure this table exists to remove.
        link = DatastoreSignedLinkEntity(
            id=uuid7(),
            code=code,
            pod_id=file.pod_id,
            created_by_user_id=created_by_user_id,
            minted_by_workload=minted_by_workload,
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
            try:
                await self._cache(link)
            except RedisError as exc:
                # Narrow on purpose. A cache that is unreachable is a dependency
                # failing and the link survives it; a `TypeError` in here would
                # be this module being wrong, and should not be swallowed as if
                # Redis were down.
                # Best-effort, because the row is already committed and the
                # allowance slot already spent. Letting this propagate lost the
                # caller a link it had been charged for and could not name, and
                # a retry just spent another slot — an agent retrying a failed
                # mint could quietly accumulate shares it never received. The
                # link works regardless: the first fetch finds nothing cached
                # and rehydrates from the record.
                logger.warning(
                    "datastore.signed_url.cache_population_failed.observed",
                    pod_id=str(file.pod_id),
                    error_type=type(exc).__name__,
                )
            signed_url = f"{settings.api_url.rstrip('/')}/s/{code}"
        return code, signed_url, expires_at, max_hits

    async def _cache(self, link: DatastoreSignedLinkEntity) -> None:
        """Put a link's claims in Redis with the counter it will spend against.

        Called on mint and again whenever a fetch finds nothing cached, which is
        what makes a lost Redis a slow first request rather than a dead link.
        """
        # The key is the enforcement on the fast path -- a hit never consults
        # the row -- so it has to lapse when the row does. An absolute expiry
        # in milliseconds, floored, so it can never land after `expires_at`:
        # a relative TTL computed here was rounded up and then started only
        # when Redis received it, so a hit could outlive the persisted expiry
        # by the rounding plus the round trip. (Whole seconds were worse: a
        # link with under a second left was dropped rather than cached, and a
        # one-second link was 404 on the mint itself.)
        expires_at = link.expires_at
        if expires_at.tzinfo is None:
            # Postgres can hand back a naive datetime; it is UTC, and
            # `timestamp()` would otherwise read it as local time.
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            return
        expires_at_ms = math.floor(expires_at.timestamp() * 1000)
        redis = await self._get_redis()
        # One script, because checking the tombstone and then writing in a
        # separate pipeline is the same check-then-act this tombstone exists to
        # close: a revoke landing between the two would set the tombstone,
        # delete the entry, and then have it written straight back.
        await redis.eval(
            _CACHE_LUA,
            2,
            self._key(link.code),
            self._tombstone_key(link.code),
            str(link.pod_id),
            link.path,
            str(link.created_by_user_id) if link.created_by_user_id else "",
            link.minted_by_workload or "",
            link.content_type,
            link.filename,
            link.max_hits,
            link.max_hits * OPEN_UNITS,
            link.max_hits * EMBEDDED_BYTES_PER_OPEN,
            expires_at_ms,
        )

    async def peek_claims(self, code: str) -> SignedUrlClaims:
        """The link's claims, charging nothing.

        Used to decide what the response *would* be — its type, size and
        validity — before committing to any bytes. A request that then turns out
        to transfer nothing (a 304, a HEAD, an unsatisfiable range) or to fail
        (the object is gone) therefore costs the link nothing, where the old
        increment-first ordering spent a download on each of them.
        """
        return await self._consume(code)

    async def consume_claims(
        self, code: str, *, units_wanted: int = 0, embedded_bytes_wanted: int = 0
    ) -> SignedUrlClaims:
        """Charge the link, returning its claims.

        ``units_wanted`` is spent against the page's opens (see ``open_units``)
        and ``embedded_bytes_wanted`` against what it may carry. Raises
        ``SignedUrlNotFound`` when the code is unknown or expired,
        ``SignedUrlExhausted`` once the opens are spent, and
        ``SignedUrlEmbeddedAllowanceSpent`` when only the allowance is.
        """
        return await self._consume(
            code, units_wanted=units_wanted, embedded_bytes_wanted=embedded_bytes_wanted
        )

    async def _consume(
        self, code: str, *, units_wanted: int = 0, embedded_bytes_wanted: int = 0
    ) -> SignedUrlClaims:
        redis = await self._get_redis()
        key = self._key(code)
        charges = (max(0, units_wanted), max(0, embedded_bytes_wanted))
        result = await redis.eval(_CONSUME_LUA, 1, key, *charges)

        if not result or int(result[0]) == -1:
            # Nothing cached. Either this code never existed, or Redis lost it
            # while the link is still live — indistinguishable from here, so ask
            # the durable record and try once more. Note the budget starts over
            # when this happens; see the module docstring for why that is the
            # side to fail on.
            if not await self._rehydrate(code):
                raise SignedUrlNotFound(code)
            result = await redis.eval(_CONSUME_LUA, 1, key, *charges)

        if not result:
            raise SignedUrlNotFound(code)
        head = int(result[0])
        if head == -1:
            raise SignedUrlNotFound(code)
        if head == -2:
            await self._retire(code, drop_cache=key)
            raise SignedUrlExhausted(code)
        if head == -3:
            raise SignedUrlEmbeddedAllowanceSpent(code)

        if head == 1:
            # This charge finished the budget. Retire the row now and still
            # serve the response it paid for: waiting for a *later* request to
            # bounce off the spent budget left the row counted as live, so the
            # last download of a link never gave its allowance slot back and a
            # caller at their limit stayed there until expiry.
            #
            # The cached entry stays. It holds `spent >= budget`, which is what
            # answers the next fetch `410 Gone` — "you have used this up" rather
            # than the `404` a missing entry would produce. Dropping it here
            # also made the fetch after that rehydrate, and the row now says
            # exhausted, so the distinction would have been lost for good.
            await self._retire(code)

        path = result[2]
        return SignedUrlClaims(
            pod_id=UUID(result[1]),
            path=path,
            created_by_user_id=UUID(result[3]) if result[3] else None,
            minted_by_workload=result[4] or None,
            content_type=result[5] or "application/octet-stream",
            filename=result[6] or path.rsplit("/", 1)[-1] or "file",
        )

    async def _retire(self, code: str, *, drop_cache: str | None = None) -> None:
        """Record that a link's budget is spent, and optionally drop its cache.

        Durably first. The other order drops the key and leaves a live row
        behind, so the very next fetch rehydrates, mints a fresh budget and
        serves the file again — the cap has to be spent somewhere that survives
        losing the counter.
        """
        async with SessionUnitOfWorkFactory(get_session_maker())() as uow:
            await SignedLinkRepository(uow).mark_exhausted(code)
            await uow.commit()
        if drop_cache is not None:
            with suppress(Exception):
                await (await self._get_redis()).delete(drop_cache)

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
        if not revoked and not await links.owns_revoked(pod_id, code):
            # Nothing of this pod's was revoked and this pod holds no revoked
            # row for the code either, so there is nothing of this pod's to
            # invalidate. The row update is scoped by `pod_id`; the Redis keys
            # below are keyed on the code alone, so doing them anyway let a
            # member of one pod knock out another pod's cached entry and hold a
            # tombstone over it — a link they have no rights to, made unusable
            # for the tombstone's lifetime and repeatably so.
            #
            # The `owns_revoked` half is what makes a retry able to finish the
            # job: an already-revoked row returns False from the update, so a
            # first attempt whose Redis half failed could never be completed by
            # a second. It is still pod-scoped, so it reopens nothing.
            return False

        # Committing first shrinks that window but does not close it: a
        # rehydrate that had already read the live row can still write the cache
        # after the delete below. The tombstone closes it — `_cache` writes only
        # when none exists, in the same step as the check.
        # Redis only from here, so the connection goes back first.
        async with connection_released(links.session):
            redis = await self._get_redis()
            try:
                async with redis.pipeline(transaction=True) as pipe:
                    pipe.setex(self._tombstone_key(code), _REVOKED_TOMBSTONE_SECONDS, 1)
                    pipe.delete(self._key(code))
                    await pipe.execute()
            except RedisError as exc:
                # Not suppressed. The row says revoked while the cached entry can
                # still be served, so reporting plain success here told the
                # caller the link was dead when it was not. Raising says the
                # revocation is incomplete and the call should be repeated —
                # which now works, because `owns_revoked` lets a retry past the
                # early return above.
                logger.warning(
                    "datastore.signed_url.revocation_cache_invalidation_failed.observed",
                    pod_id=str(pod_id),
                    error_type=type(exc).__name__,
                )
                raise SignedUrlRevocationIncomplete(code) from exc
        return revoked


_store: SignedUrlStore | None = None


def get_signed_url_store() -> SignedUrlStore:
    global _store
    if _store is None:
        _store = SignedUrlStore()
    return _store
