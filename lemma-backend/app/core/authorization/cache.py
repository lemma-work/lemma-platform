"""Authorization role-snapshot cache (Redis-backed).

Shared across the API, the worker, and replicas so a grant/role change
invalidates the snapshot everywhere at once — an in-process dict would leave
other replicas serving stale (revoked) authorization until the TTL elapsed.
Redis being unavailable degrades to a cache miss (the snapshot is re-derived
from the DB), so it never fails an authorization check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import PrincipalRef
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache


@dataclass(frozen=True, slots=True)
class RoleSnapshot:
    organization_id: UUID | None
    pod_id: UUID | None
    role_ids: frozenset[UUID]
    role_names: frozenset[str]
    permission_ids: frozenset[str]
    principal_refs: frozenset[PrincipalRef]
    grant_principal_sets: tuple[frozenset[PrincipalRef], ...]


_role_cache: RedisJsonCache | None = None


def _get_role_cache() -> RedisJsonCache | None:
    global _role_cache
    ttl = settings.authorization_role_cache_ttl_seconds
    if ttl <= 0:
        return None
    if _role_cache is None or _role_cache._ttl_seconds != ttl:
        _role_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="authz:role-snapshot",
            ttl_seconds=ttl,
        )
    return _role_cache


def _snapshot_suffix(
    user_id: UUID, organization_id: UUID | None, pod_id: UUID | None
) -> str:
    return f"{user_id}:{organization_id or '-'}:{pod_id or '-'}"


def _principal_to_json(p: PrincipalRef) -> dict:
    return {"type": p.type, "id": str(p.id)}


def _principal_from_json(d: dict) -> PrincipalRef:
    return PrincipalRef(type=d["type"], id=UUID(d["id"]))


def _serialize(snapshot: RoleSnapshot) -> str:
    return json.dumps(
        {
            "organization_id": str(snapshot.organization_id)
            if snapshot.organization_id
            else None,
            "pod_id": str(snapshot.pod_id) if snapshot.pod_id else None,
            "role_ids": [str(x) for x in snapshot.role_ids],
            "role_names": list(snapshot.role_names),
            "permission_ids": list(snapshot.permission_ids),
            "principal_refs": [_principal_to_json(p) for p in snapshot.principal_refs],
            "grant_principal_sets": [
                [_principal_to_json(p) for p in group]
                for group in snapshot.grant_principal_sets
            ],
        }
    )


def _deserialize(payload: str) -> RoleSnapshot:
    d = json.loads(payload)
    return RoleSnapshot(
        organization_id=UUID(d["organization_id"]) if d["organization_id"] else None,
        pod_id=UUID(d["pod_id"]) if d["pod_id"] else None,
        role_ids=frozenset(UUID(x) for x in d["role_ids"]),
        role_names=frozenset(d["role_names"]),
        permission_ids=frozenset(d["permission_ids"]),
        principal_refs=frozenset(_principal_from_json(p) for p in d["principal_refs"]),
        grant_principal_sets=tuple(
            frozenset(_principal_from_json(p) for p in group)
            for group in d["grant_principal_sets"]
        ),
    )


async def get_role_snapshot(
    *,
    user_id: UUID,
    organization_id: UUID | None,
    pod_id: UUID | None,
) -> RoleSnapshot | None:
    cache = _get_role_cache()
    if cache is None:
        return None
    try:
        payload = await cache.get_raw(
            _snapshot_suffix(user_id, organization_id, pod_id)
        )
    except Exception:
        return None  # Redis unavailable -> miss; the snapshot is re-derived.
    if not payload:
        return None
    try:
        return _deserialize(payload)
    except Exception:
        return None  # Forward-incompatible payload -> treat as a miss.


async def set_role_snapshot(
    *,
    user_id: UUID,
    snapshot: RoleSnapshot,
) -> None:
    cache = _get_role_cache()
    if cache is None:
        return
    try:
        await cache.set_raw(
            _snapshot_suffix(user_id, snapshot.organization_id, snapshot.pod_id),
            _serialize(snapshot),
        )
    except Exception:
        pass  # Redis unavailable -> skip caching.


async def invalidate_role_snapshot_cache(
    *,
    organization_id: UUID | None = None,
    pod_id: UUID | None = None,
    user_id: UUID | None = None,
) -> None:
    """Drop cached role snapshots after a grant/role mutation.

    Clears the whole snapshot cache (a safe superset of the
    organization/pod/user scope): role mutations are infrequent admin operations,
    and over-clearing only forces a DB re-derivation on the next access — never
    stale authorization. The scope args are accepted for call-site clarity.
    """
    _ = (organization_id, pod_id, user_id)
    cache = _get_role_cache()
    if cache is None:
        return
    try:
        await cache.clear_prefix()
    except Exception:
        pass
