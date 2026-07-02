"""Session-scoped approval store for workload actions (Redis-backed).

When a user resolves a ``request_approval`` with APPROVE_FOR_SESSION, the
approved permission is recorded here keyed to the conversation and the
workload actor. The authorizer then honors it as an ephemeral grant — most
importantly for DESTRUCTIVE_ACTIONS, which no workload may perform by default.

Redis-backed (shared across replicas, like the role-snapshot cache) with a
config TTL. Redis being unavailable degrades to "no approval" — the safe
direction: the agent re-prompts instead of acting unapproved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

_approval_cache: RedisJsonCache | None = None


def _get_approval_cache() -> RedisJsonCache | None:
    global _approval_cache
    ttl = settings.session_approval_ttl_seconds
    if ttl <= 0:
        return None
    if _approval_cache is None or _approval_cache._ttl_seconds != ttl:
        _approval_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="authz:session-approval",
            ttl_seconds=ttl,
        )
    return _approval_cache


def _suffix(session_id: str, workload_actor_id: str, permission_id: str) -> str:
    return f"{session_id}:{workload_actor_id}:{permission_id}"


async def record_session_approval(
    *,
    session_id: str,
    workload_actor_id: str,
    permission_id: str,
    resolved_by_user_id: UUID,
) -> None:
    """Persist an APPROVE_FOR_SESSION decision for one permission."""
    cache = _get_approval_cache()
    if cache is None:
        return
    try:
        await cache.set_json(
            _suffix(session_id, workload_actor_id, permission_id),
            {
                "resolved_by_user_id": str(resolved_by_user_id),
                "approved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.warning(
            "Session-approval store unavailable; approval for %s by workload %s "
            "will not persist for the session (each use re-prompts).",
            permission_id,
            workload_actor_id,
            exc_info=True,
        )


async def has_session_approval(
    *,
    session_id: str | None,
    workload_actor_id: str | None,
    permission_id: str,
) -> bool:
    """True when the user approved this action type for this workload+session."""
    if not session_id or not workload_actor_id:
        return False
    cache = _get_approval_cache()
    if cache is None:
        return False
    try:
        payload = await cache.get_json(
            _suffix(session_id, workload_actor_id, permission_id)
        )
    except Exception:
        logger.warning(
            "Session-approval store unavailable; treating %s as unapproved for "
            "workload %s (safe direction).",
            permission_id,
            workload_actor_id,
            exc_info=True,
        )
        return False
    return payload is not None
