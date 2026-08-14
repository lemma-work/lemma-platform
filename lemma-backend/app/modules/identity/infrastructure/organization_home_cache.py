"""Short-lived cache for the organization landing page.

A landing page is re-fetched on every visit and on every tab focus, while its
contents — pods, apps, agents — change on a human timescale. Serving it from
Redis for a few seconds turns a burst of navigation into one read, which matters
most for the people it costs most: someone in many organizations fetches this
once per organization.

Keyed by user as well as organization. Two members of the same organization see
different pods, so an organization-only key would hand one person's listing to
another — a correctness bug wearing a performance cache's clothes.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.cache.resilient_cache import ResilientJsonCache

_home_cache: ResilientJsonCache | None = None


def _get_cache() -> ResilientJsonCache | None:
    global _home_cache
    ttl = settings.organization_home_cache_ttl_seconds
    if ttl <= 0:
        return None
    if _home_cache is None or _home_cache.ttl_seconds != ttl:
        _home_cache = ResilientJsonCache(
            name="organization_home_cache",
            key_prefix="identity:org-home",
            redis_url=settings.redis_url,
            ttl_seconds=ttl,
        )
    return _home_cache


def _suffix(organization_id: UUID, user_id: UUID) -> str:
    return f"{organization_id}:{user_id}"


def _decode(payload: str) -> dict[str, Any]:
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("organization home payload is not an object")
    return decoded


async def get_cached_organization_home(
    *, organization_id: UUID, user_id: UUID
) -> dict[str, Any] | None:
    cache = _get_cache()
    if cache is None:
        return None
    return await cache.get(_suffix(organization_id, user_id), _decode)


async def set_cached_organization_home(
    *, organization_id: UUID, user_id: UUID, payload: dict[str, Any]
) -> None:
    cache = _get_cache()
    if cache is None:
        return
    await cache.set(
        _suffix(organization_id, user_id),
        json.dumps(payload, separators=(",", ":")),
    )
