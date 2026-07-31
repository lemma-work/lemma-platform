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

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
_store_incident = DependencyIncident("session_approval_store", logger=logger)

_approval_cache: RedisJsonCache | None = None


def exact_command_permission_id(tool_name: str, args: dict | None) -> str:
    """A stable "permission id" identifying one exact (tool_name, args) call.

    Used as a ``has_session_approval``/``record_session_approval`` key so a
    ``request_approval`` call with no structured ``permission_ids`` (e.g.
    ``exec_command``/``execute_python`` — these have no authorization gate at
    all, so there's nothing to derive a category from) still gets SOME
    APPROVE_FOR_SESSION reuse: approving one exact call lets the agent repeat
    that literal call again in the same conversation without re-prompting.

    Deliberately exact-match only, never a prefix/substring match: shell
    metacharacters (``;``, ``&&``, ``|``, backticks, command substitution) let
    an attacker smuggle extra commands after a prefix that looks identical to
    one the user already approved, so anything looser than an exact match on
    the full argument set would be a real injection vector.
    """
    canonical = json.dumps(
        args or {}, sort_keys=True, separators=(",", ":"), default=str
    )
    digest = hashlib.sha256(f"{tool_name}:{canonical}".encode("utf-8")).hexdigest()[:32]
    return f"exact_command:{tool_name}:{digest}"


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
    except Exception as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


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
    except Exception as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
        return False
    _store_incident.record_success()
    return payload is not None
