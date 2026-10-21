"""A caller's searchable-file split, kept in Redis until the pod's permissions move.

Working out which of a pod's searchable files a caller may read is two indexed
reads and a pass over the pod's searchable rows -- cheap per search, but paid
on every search, and an agent searches in bursts. The answer depends on only
two things: who is asking, and the pod's permission state. So it is keyed by
both -- a hash of everything about the caller the read rule consults, and the
pod's authorization epoch, which every grant, visibility, membership, create,
delete, move and completed indexing in the pod bumps (``authz_epoch``). An
entry under a retired epoch is never read again and expires on its own.

Bounded in size: a split with more ids than the ceiling is not stored, since
moving it through Redis would cost about what recomputing it does. Redis being
unavailable is a cache miss.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from redis.exceptions import RedisError

from app.core.authorization.context import Context, PrincipalRef
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.search_scope import SearchablePartition

logger = get_logger(__name__)

_READ = "folder.read"
_cache: RedisJsonCache[str] | None = None


def _store() -> RedisJsonCache[str]:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            settings.redis_url,
            "datastore:search-scope",
            max(1, datastore_settings.datastore_search_scope_cache_ttl_seconds),
        )
    return _cache


def _refs(refs: frozenset[PrincipalRef]) -> list[str]:
    return sorted(f"{ref.type}:{ref.id}" for ref in refs)


def caller_key(ctx: Context, *, walk_ancestors: bool) -> str:
    """Everything about the caller the read rule consults, hashed.

    Two callers with the same key get the same split by construction: these
    are exactly the inputs of ``file_read_rule`` and the ancestor walk.
    """
    actor_type = getattr(ctx.actor_type, "value", ctx.actor_type)
    parts = {
        "actor": str(actor_type),
        "superuser": ctx.is_superuser,
        "outsider": ctx.is_outsider,
        "user": str(ctx.user_id) if ctx.user_id else None,
        "pod": str(ctx.pod_id) if ctx.pod_id else None,
        "read": ctx.has_permission(_READ),
        "principals": _refs(ctx.principal_refs),
        "groups": [_refs(group) for group in ctx.grant_principal_sets],
        "workload": _refs(ctx.workload_principal_refs),
        "walk": walk_ancestors,
    }
    digest = hashlib.sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
    )
    return digest.hexdigest()


def _suffix(pod_id: UUID, epoch: str, key: str) -> str:
    return f"{pod_id}:{epoch}:{key}"


def _decode(raw: str | bytes) -> SearchablePartition:
    body = json.loads(raw)
    return SearchablePartition(
        readable=frozenset(UUID(item) for item in body["readable"]),
        hidden=frozenset(UUID(item) for item in body["hidden"]),
    )


def _encode(partition: SearchablePartition) -> str:
    return json.dumps(
        {
            "readable": sorted(str(item) for item in partition.readable),
            "hidden": sorted(str(item) for item in partition.hidden),
        },
        separators=(",", ":"),
    )


async def cached_partition(
    pod_id: UUID, epoch: str, key: str
) -> SearchablePartition | None:
    try:
        raw = await _store().get_raw(_suffix(pod_id, epoch, key))
    except RedisError, OSError:
        logger.debug("datastore.search_scope_cache.read.diagnostic", exc_info=True)
        return None
    if raw is None:
        return None
    # Up to `datastore_search_scope_cache_max_ids` ids: off the loop.
    return await run_blocking(_decode, raw)


async def store_partition(
    pod_id: UUID, epoch: str, key: str, partition: SearchablePartition
) -> None:
    size = len(partition.readable) + len(partition.hidden)
    if size > datastore_settings.datastore_search_scope_cache_max_ids:
        return
    body = await run_blocking(_encode, partition)
    try:
        await _store().set_raw(
            _suffix(pod_id, epoch, key),
            body,
            ttl_seconds=datastore_settings.datastore_search_scope_cache_ttl_seconds,
        )
    except RedisError, OSError:
        logger.debug("datastore.search_scope_cache.write.diagnostic", exc_info=True)
