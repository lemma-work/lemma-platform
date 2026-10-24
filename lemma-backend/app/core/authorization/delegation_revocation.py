"""Delegation-token revocation set (Redis-backed).

A delegated workload token (agent/function) is a signed session that stays valid
until it expires. When the workload loses its standing authority — most
importantly when the agent/function is deleted — its in-flight tokens must stop
working before they expire on their own. Revoking the workload's actor id here
blocks every delegated token minted for it until the entry's TTL (configured to
be >= the max access-token lifetime) elapses.

Redis-backed (shared across replicas, like the role-snapshot and session-approval
caches). Redis being unavailable degrades to "not revoked" — availability over
strictness, consistent with the other authz caches — but logs loudly so an
outage is visible. (Resource-grant removal needs no revocation: workload grants
are queried live on every check, so they take effect immediately.)

**Ending a delegation session** is the narrower cut: every short-lived token
minted for one session -- a conversation's, in a workspace sandbox -- before
the moment it ended. Keyed by time rather than by token, so nothing has to
remember which tokens were handed out, and a conversation that resumes mints
new ones that are not caught by its earlier end. Only tokens with a lifetime of
their own (``CLAIM_EXPIRES_AT``) are checked: they are the ones a sandbox
holds, and the check costs a read on every request that presents one.
"""

from __future__ import annotations

import time
from uuid import UUID

from redis.exceptions import RedisError

from app.core.authorization.delegation import DelegationClaims
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
_store_incident = DependencyIncident("delegation_revocation_store", logger=logger)

_revocation_cache: RedisJsonCache | None = None
_session_end_cache: RedisJsonCache | None = None


def _get_revocation_cache() -> RedisJsonCache | None:
    global _revocation_cache
    ttl = settings.delegation_revocation_ttl_seconds
    if ttl <= 0:
        return None
    if _revocation_cache is None or _revocation_cache._ttl_seconds != ttl:
        _revocation_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="authz:delegation-revoked",
            ttl_seconds=ttl,
        )
    return _revocation_cache


def _get_session_end_cache() -> RedisJsonCache | None:
    # The same lifetime as an actor's revocation, for its reason: an entry has
    # to outlast every token it refuses, and none outlives the access token.
    global _session_end_cache
    ttl = settings.delegation_revocation_ttl_seconds
    if ttl <= 0:
        return None
    if _session_end_cache is None or _session_end_cache._ttl_seconds != ttl:
        _session_end_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="authz:delegation-session-ended",
            ttl_seconds=ttl,
        )
    return _session_end_cache


async def revoke_delegation(*, actor_id: UUID) -> None:
    """Block every in-flight delegated token minted for ``actor_id``."""
    cache = _get_revocation_cache()
    if cache is None:
        return
    try:
        await cache.set_json(str(actor_id), {"revoked": True})
    except Exception as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def is_delegation_revoked(*, actor_id: UUID) -> bool:
    """True when a delegated token for ``actor_id`` has been revoked."""
    cache = _get_revocation_cache()
    if cache is None:
        return False
    try:
        payload = await cache.get_json(str(actor_id))
    except Exception as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
        return False
    _store_incident.record_success()
    return payload is not None


async def end_delegation_session(
    *, session_id: str, ended_at: float | None = None
) -> None:
    """Refuse every short-lived token minted for ``session_id`` before
    ``ended_at`` (now, by default).

    Never moves an end earlier, and a caller that may run late -- a retried
    work item -- passes the moment the session really ended: an end recorded
    at retry time would also refuse the tokens of a run that began since.
    """
    cache = _get_session_end_cache()
    if cache is None:
        return
    moment = time.time() if ended_at is None else ended_at
    try:
        existing = await cache.get_json(session_id)
        previous = existing.get("ended_at") if isinstance(existing, dict) else None
        if isinstance(previous, (int, float)) and previous >= moment:
            return
        await cache.set_json(session_id, {"ended_at": moment})
    except (RedisError, OSError, ValueError) as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def delegation_session_ended(*, session_id: str, issued_at: float) -> bool:
    """True when ``session_id`` ended after a token issued at ``issued_at``."""
    cache = _get_session_end_cache()
    if cache is None:
        return False
    try:
        payload = await cache.get_json(session_id)
    except (RedisError, OSError, ValueError) as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
        return False
    _store_incident.record_success()
    ended_at = payload.get("ended_at") if isinstance(payload, dict) else None
    return isinstance(ended_at, (int, float)) and issued_at < ended_at


def delegation_expired(claims: DelegationClaims, *, now: float | None = None) -> bool:
    """Whether a token with a lifetime of its own has outlived it."""
    return (
        claims.expires_at is not None
        and (time.time() if now is None else now) >= claims.expires_at
    )
