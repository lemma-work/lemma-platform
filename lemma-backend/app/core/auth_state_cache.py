"""Account-standing cache for the per-request authentication check (Redis-backed).

``verify_auth`` has to know three things about the caller on every authenticated
request — active, verified, not deleted — and it read them by opening a database
session of its own, separate from the request's unit of work, with no caching at
all. Measured against the real connection shape, authentication was 5.8ms of a
10.9ms pod-list request, and this read is one of its two queries plus a whole
extra pool checkout. Every authenticated endpoint paid it.

Shared through Redis rather than an in-process dict for the same reason the role
snapshot is: an API with several replicas would otherwise keep serving a
deactivated account until each replica's own copy expired. Redis being
unavailable degrades to a miss (the standing is re-read from the database), so
this can slow a request down but never wrongly admit one.

Staleness is bounded by ``auth_state_cache_ttl_seconds`` and cut short by
:func:`invalidate_auth_state` wherever standing actually changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.cache.resilient_cache import ResilientJsonCache


@dataclass(frozen=True, slots=True)
class AccountStanding:
    """Whether an account may make a request at all."""

    is_active: bool
    is_verified: bool
    is_deleted: bool


_auth_state_cache: ResilientJsonCache | None = None


def _get_cache() -> ResilientJsonCache | None:
    global _auth_state_cache
    ttl = settings.auth_state_cache_ttl_seconds
    if ttl <= 0:
        return None
    if _auth_state_cache is None or _auth_state_cache.ttl_seconds != ttl:
        _auth_state_cache = ResilientJsonCache(
            name="auth_state_cache",
            key_prefix="auth:account-standing",
            redis_url=settings.redis_url,
            ttl_seconds=ttl,
        )
    return _auth_state_cache


def _decode(payload: str) -> AccountStanding:
    decoded = json.loads(payload)
    return AccountStanding(
        is_active=bool(decoded["is_active"]),
        is_verified=bool(decoded["is_verified"]),
        is_deleted=bool(decoded["is_deleted"]),
    )


async def get_account_standing(user_id: UUID) -> AccountStanding | None:
    """Cached standing, or None on a miss, a stale format, or a Redis outage."""
    cache = _get_cache()
    if cache is None:
        return None
    return await cache.get(str(user_id), _decode)


async def set_account_standing(user_id: UUID, standing: AccountStanding) -> None:
    cache = _get_cache()
    if cache is None:
        return
    await cache.set(
        str(user_id),
        json.dumps(
            {
                "is_active": standing.is_active,
                "is_verified": standing.is_verified,
                "is_deleted": standing.is_deleted,
            },
            separators=(",", ":"),
        ),
    )


async def invalidate_auth_state(user_id: UUID) -> None:
    """Drop one account's cached standing, after that standing changed.

    Called wherever ``is_active``/``is_verified``/``is_deleted`` move, so a
    deactivation takes effect on the next request rather than at the end of the
    TTL. The TTL remains the backstop for anything that forgets to call this.
    """
    cache = _get_cache()
    if cache is None:
        return
    await cache.delete(str(user_id))
