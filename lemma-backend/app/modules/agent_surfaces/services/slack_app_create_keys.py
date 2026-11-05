"""Who is creating which teammate's Slack app, and what a key already made.

Creating the app calls Slack, so two requests for the same agent and workspace
must not both reach it. Two Redis entries keep them apart:

* **The creation lease**, per (agent, workspace), held while one request runs:
  a second is told to wait rather than making a second app.
* **The ``Idempotency-Key`` result**, per (pod, member, key), for a day: a
  retry with the key answers with the app the first request made, even once
  that app has been authorised and its pending row is gone.

Without Redis both are skipped, and the database is the last guard: the
pending row is unique per agent and workspace, and the request that loses that
race deletes the app it made (``SlackAppProvisioner``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: How long a key names the app it made.
KEY_TTL_SECONDS = 24 * 60 * 60
#: Longer than a create ever runs: two Slack calls, a rotation, and an install.
LEASE_TTL_SECONDS = 120

_cache: RedisJsonCache[str] | None = None


def _keys() -> RedisJsonCache[str]:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surfaces:slack-app-create",
            ttl_seconds=KEY_TTL_SECONDS,
        )
    return _cache


@dataclass(frozen=True, slots=True)
class MadeApp:
    app_id: str
    auth_config_id: UUID


@dataclass(frozen=True, slots=True)
class Lease:
    """``held`` is false only when another request holds it; without Redis
    nobody does, and ``owner`` is ``None``."""

    suffix: str
    owner: str | None
    held: bool


def _lease_suffix(agent_id: UUID, team_id: str) -> str:
    return f"lease:{agent_id}:{team_id}"


def _key_suffix(pod_id: UUID, user_id: UUID, key: str) -> str:
    return f"key:{pod_id}:{user_id}:{hashlib.sha256(key.encode()).hexdigest()}"


async def take_lease(agent_id: UUID, team_id: str) -> Lease:
    suffix = _lease_suffix(agent_id, team_id)
    owner = uuid4().hex
    try:
        held = await _keys().set_raw_if_absent(
            suffix, owner, ttl_seconds=LEASE_TTL_SECONDS
        )
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.slack_app_creation.create_key_unavailable.degraded",
            exc_info=True,
        )
        return Lease(suffix=suffix, owner=None, held=True)
    return Lease(suffix=suffix, owner=owner, held=bool(held))


async def release_lease(lease: Lease) -> None:
    if lease.owner is None or not lease.held:
        return
    try:
        await _keys().delete_if_value(lease.suffix, lease.owner)
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.slack_app_creation.create_key_unavailable.degraded",
            exc_info=True,
        )


async def made_with_key(pod_id: UUID, user_id: UUID, key: str | None) -> MadeApp | None:
    if key is None:
        return None
    try:
        known = await _keys().get_raw(_key_suffix(pod_id, user_id, key))
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.slack_app_creation.create_key_unavailable.degraded",
            exc_info=True,
        )
        return None
    value = known.decode() if isinstance(known, bytes) else known
    if not value or " " not in value:
        return None
    app_id, auth_config_id = value.split(" ", 1)
    try:
        return MadeApp(app_id=app_id, auth_config_id=UUID(auth_config_id))
    except ValueError:
        return None


async def remember_key(
    pod_id: UUID, user_id: UUID, key: str | None, made: MadeApp
) -> None:
    if key is None:
        return
    try:
        await _keys().set_raw(
            _key_suffix(pod_id, user_id, key), f"{made.app_id} {made.auth_config_id}"
        )
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.slack_app_creation.create_key_unavailable.degraded",
            exc_info=True,
        )
