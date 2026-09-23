"""What a sandbox that is being brought up is doing, for anyone who asks.

The ensure loop in `sandbox_service` already knows when a sandbox is not ready
and why -- a fabric saying "still downloading <image>" after an update, or
simply "not started yet" -- and logged it where nobody who was waiting could
see. Recorded here instead, with a short expiry, so a status request on any
worker can say "downloading" or "starting" rather than let the file explorer and
the browser pane look broken while the computer is only coming up.

In Redis because several workers serve one person: the one waiting on the
ensure is rarely the one answering the status request.
"""

from __future__ import annotations

import re
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Longer than the ensure loop's longest wait between attempts, so a phase does
#: not blink out between two retries, and short enough that a worker that died
#: mid-ensure leaves nothing behind for long.
_PHASE_TTL_SECONDS = 30

Phase = Literal["downloading", "starting"]

#: Redis unreachable or refusing, or a value that is not what was written.
_STORE_FAILURES = (RedisError, OSError, ValueError)


class SandboxPhase(TypedDict):
    phase: Phase
    detail: str
    #: Megabytes fetched and in total, while the guest can say.
    done_mb: NotRequired[int]
    total_mb: NotRequired[int]


#: The guest's "still downloading <image> (412 MB of 980 MB)".
_DOWNLOADED = re.compile(r"\((\d+) MB of (\d+) MB\)")


_cache: RedisJsonCache | None = None


def _phases() -> RedisJsonCache:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="workspace:sandbox-phase",
            ttl_seconds=_PHASE_TTL_SECONDS,
        )
    return _cache


def _use_store_for_tests(redis: object | None) -> None:
    """Point the phase store at `redis`, or back at the configured one."""
    global _cache
    if redis is None:
        _cache = None
        return
    _cache = RedisJsonCache(
        redis_url=settings.redis_url,
        key_prefix="workspace:sandbox-phase",
        ttl_seconds=_PHASE_TTL_SECONDS,
    )
    _cache._redis = redis  # type: ignore[assignment]


def phase_for(reason: str) -> SandboxPhase:
    """The phase a fabric's "not ready yet" describes, in words for a person."""
    if "still downloading" in reason:
        phase: SandboxPhase = {
            "phase": "downloading",
            "detail": "Downloading the workspace image. The first start after an update takes a few minutes.",
        }
        if found := _DOWNLOADED.search(reason):
            phase["done_mb"], phase["total_mb"] = int(found[1]), int(found[2])
        return phase
    return {"phase": "starting", "detail": "Starting your computer."}


async def record_phase(sandbox_id: UUID, reason: str) -> None:
    """Note why the sandbox is not ready yet. Never fails the ensure it describes."""
    try:
        await _phases().set_json(str(sandbox_id), phase_for(reason))
    except _STORE_FAILURES:  # A progress note is not worth an ensure.
        logger.warning(
            "workspace.sandbox_progress.record_failed.degraded",
            sandbox_id=str(sandbox_id),
            exc_info=True,
        )


async def clear_phase(sandbox_id: UUID) -> None:
    try:
        await _phases().delete(str(sandbox_id))
    except _STORE_FAILURES:  # Expires on its own within the TTL.
        logger.warning(
            "workspace.sandbox_progress.clear_failed.degraded",
            sandbox_id=str(sandbox_id),
            exc_info=True,
        )


async def current_phase(sandbox_id: UUID) -> SandboxPhase | None:
    try:
        found = await _phases().get_json(str(sandbox_id))
    except _STORE_FAILURES:  # Unknown is an honest answer to a status poll.
        logger.warning(
            "workspace.sandbox_progress.read_failed.degraded",
            sandbox_id=str(sandbox_id),
            exc_info=True,
        )
        return None
    if isinstance(found, dict) and found.get("phase") in ("downloading", "starting"):
        phase: SandboxPhase = {
            "phase": found["phase"],
            "detail": str(found.get("detail") or ""),
        }
        for key in ("done_mb", "total_mb"):
            if isinstance(value := found.get(key), int) and not isinstance(value, bool):
                phase[key] = value
        return phase
    return None
