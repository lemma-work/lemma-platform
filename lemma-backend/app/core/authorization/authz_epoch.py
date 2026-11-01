"""A counter per pod that moves whenever who-may-read-what in it may have changed.

Things derived from a pod's permissions -- the set of files a caller may
search, above all -- are expensive to work out and cheap to keep. Keeping them
is only safe if every change that could alter them invalidates them, and those
changes are scattered: grants, visibility, membership and roles, and files
appearing, leaving or moving. Rather than have each of those sites know every
cache, each bumps this counter, and a cache keys its entries by it. An entry
under an old epoch is simply never read again and expires on its own.

There is a pod epoch and a global one. An invalidation that cannot name a pod
(a role definition changed, a principal's snapshots dropped across every pod)
bumps the global one, which every pod's key includes.

The pod's part is two counters. **Grants** moves when who may do what moved:
grants, roles, membership. **Files** moves when the pod's set of files moved
-- one created, deleted, indexed, moved or made visible -- which changes what
a search may reach but not who may read a channel, a table or a conversation.
``pod_authz_epoch`` is all of them, what a search-scope cache keys by;
``pod_grants_epoch`` leaves files out, so a live hub re-checking its readers
when the epoch moves is not woken by every upload.

Bumped after commit, never inside the transaction: a reader that works the
answer out between a bump and the commit it announces would cache the old
answer under the new epoch. After commit, the worst case is a reader caching
under the epoch that is about to be retired. Redis being unavailable reads as
"no epoch": callers then do not cache at all, which is slower and never wrong.
"""

from __future__ import annotations

import time
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.db.session_uow import active_uow
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
_incident = DependencyIncident("authorization_epoch", logger=logger)

_PREFIX = "authz:epoch"
_GLOBAL = f"{_PREFIX}:global"
_GRANTS = "pod"
_FILES = "files"
#: Long enough to outlive any entry keyed by it; refreshed on every bump.
_EPOCH_TTL_SECONDS = 30 * 24 * 3600
# Redis INCR is a signed 64-bit operation. The maximum value itself cannot be
# incremented, so treat it as needing repair before it reaches a mutation path.
_MAX_INCREMENTABLE_EPOCH = "9223372036854775807"


def _pod_key(pod_id: UUID, part: str = _GRANTS) -> str:
    return f"{_PREFIX}:{part}:{pod_id}"


def _seed() -> str:
    # A lost counter must not restart at a value an old entry was keyed by, so
    # a missing epoch starts from the clock rather than from zero.
    return str(time.time_ns())


def _epoch_value(value: bytes | str | None) -> str | None:
    """A Redis epoch must be the decimal string that INCR can advance."""
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        return None
    normalized = value.lstrip("0") or "0"
    # Do not merely normalize the returned cache key: the stored Redis value
    # must itself be safe for INCR. Repair non-canonical integer strings via
    # the compare-and-set path before any caller can rely on them.
    if normalized != value:
        return None
    if len(normalized) > len(_MAX_INCREMENTABLE_EPOCH) or (
        len(normalized) == len(_MAX_INCREMENTABLE_EPOCH)
        and normalized >= _MAX_INCREMENTABLE_EPOCH
    ):
        return None
    return value


_REPAIR_INVALID_EPOCH = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
end
return 0
"""


async def _repair_invalid_epoch(
    redis: Redis, key: str, observed: bytes | str | None
) -> None:
    """Repair only the exact value observed, never a concurrent valid bump."""
    if observed is None:
        await redis.set(key, _seed(), ex=_EPOCH_TTL_SECONDS, nx=True)
        return
    await redis.eval(
        _REPAIR_INVALID_EPOCH,
        1,
        key,
        observed,
        _seed(),
        _EPOCH_TTL_SECONDS,
    )


async def _epoch_of(keys: list[str]) -> str | None:
    redis = get_redis(url=settings.redis_url)
    try:
        values = await redis.mget(keys)
        for index, value in enumerate(values):
            if value is None:
                await redis.set(keys[index], _seed(), ex=_EPOCH_TTL_SECONDS, nx=True)
        if any(value is None for value in values):
            values = await redis.mget(keys)
        invalid_indexes = [
            index
            for index, value in enumerate(values)
            if _epoch_value(value) is None
        ]
        for index in invalid_indexes:
            # A non-decimal value cannot be advanced by Redis INCR. Replace it
            # with a fresh, time-derived epoch so old cache entries are retired
            # and subsequent permission mutations can invalidate normally.
            await _repair_invalid_epoch(redis, keys[index], values[index])
        if invalid_indexes:
            logger.warning(
                "authorization.epoch_value_repaired.degraded",
                invalid_key_count=len(invalid_indexes),
            )
            values = await redis.mget(keys)
    except (RedisError, OSError) as exc:
        _incident.record_failure(error_type=type(exc).__name__)
        return None
    decoded = [_epoch_value(value) for value in values]
    if any(value is None for value in decoded):
        _incident.record_failure(error_type="InvalidEpochValue")
        return None
    _incident.record_success()
    return ":".join(value for value in decoded if value is not None)


async def pod_authz_epoch(pod_id: UUID) -> str | None:
    """The pod's whole epoch -- global, grants and files -- or None without
    Redis."""
    return await _epoch_of([_GLOBAL, _pod_key(pod_id), _pod_key(pod_id, _FILES)])


async def pod_grants_epoch(pod_id: UUID) -> str | None:
    """The pod's epoch without its files: it moves when who may do what in the
    pod may have, never on a file write. None without Redis."""
    return await _epoch_of([_GLOBAL, _pod_key(pod_id)])


async def _bump(key: str, pod_id: UUID | None) -> None:
    redis = get_redis(url=settings.redis_url)
    try:
        await redis.set(key, _seed(), ex=_EPOCH_TTL_SECONDS, nx=True)
        # The read path can repair a malformed counter, but invalidations are
        # also allowed to run before any reader has inspected that key. Repair
        # here as well so a permission mutation cannot get stuck behind a bad
        # Redis value. The compare-and-set repair never overwrites a concurrent
        # valid increment.
        for _attempt in range(3):
            current = await redis.get(key)
            if _epoch_value(current) is None:
                await _repair_invalid_epoch(redis, key, current)
                current = await redis.get(key)
                if _epoch_value(current) is None:
                    continue
            try:
                await redis.incr(key)
            except ResponseError as exc:
                # Another worker can advance max-1 to max after our read. Redis
                # refuses the next INCR; repair only that observed terminal
                # value, then retry against the fresh counter.
                if "overflow" not in str(exc).lower():
                    raise
                current = await redis.get(key)
                if _epoch_value(current) is not None:
                    raise
                await _repair_invalid_epoch(redis, key, current)
                continue
            await redis.expire(key, _EPOCH_TTL_SECONDS)
            _incident.record_success()
            return
        raise RedisError("Authorization epoch remained invalid during repair")
    except (RedisError, OSError) as exc:
        # Entries keyed by the old epoch live out their TTL, and live hubs
        # re-check their readers on a timer as well as when the epoch moves:
        # those bound the staleness when Redis is unreachable for a bump. Each
        # lost bump is logged, being a permission change nobody was told of.
        _incident.record_failure(error_type=type(exc).__name__)
        logger.warning(
            "authorization.epoch_bump_failed.degraded",
            pod_id=str(pod_id) if pod_id is not None else None,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return


async def bump_pod_authz_epoch(pod_id: UUID | None) -> None:
    """Who may do what in the pod may have moved (in every pod, for None):
    retire every entry keyed by its epoch, and have its live hubs re-check."""
    await _bump(_GLOBAL if pod_id is None else _pod_key(pod_id), pod_id)


async def bump_pod_files_epoch(pod_id: UUID) -> None:
    """The pod's set of files moved: retire what was keyed by the old set (a
    search's scope). Its grants epoch stays, so live hubs are not woken."""
    await _bump(_pod_key(pod_id, _FILES), pod_id)


_PENDING_BUMPS = "lemma_authz_epoch_bumps"


def bump_after_commit(
    session: AsyncSession, pod_id: UUID | None, *, files: bool = False
) -> None:
    """Bump once the session's transaction commits; once per pod per transaction.

    ``files``: what moved is the pod's set of files, not who may do what
    (``bump_pod_files_epoch``).

    Synchronous on purpose: it only registers the bump, so a caller holding a
    session never waits on Redis. A session with no unit of work to defer to
    gets no bump -- anything keyed by the old epoch lives out its short TTL,
    which is the same bound as a bump lost to a Redis outage.
    """
    uow = active_uow(session)
    if uow is None:
        return
    pending: set[tuple[UUID | None, bool]] = session.info.setdefault(
        _PENDING_BUMPS, set()
    )
    key = (pod_id, files and pod_id is not None)
    if key in pending:
        return
    pending.add(key)

    async def _bump_now() -> None:
        pending.discard(key)
        if key[1] and pod_id is not None:
            await bump_pod_files_epoch(pod_id)
        else:
            await bump_pod_authz_epoch(pod_id)

    uow.after_commit(_bump_now)
