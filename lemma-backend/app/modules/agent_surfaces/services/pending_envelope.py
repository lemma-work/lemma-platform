"""Parts a one-reply surface has collected but has not sent yet.

A chat surface delivers each envelope as it is ready. Email cannot: the person
gets one composed reply, so anything the run wants to show has to become part of
it. Until now that meant ``display_resource`` on an email surface returned
``success=True, "FILE resource ready for display."`` and delivered nothing --
the model believed it had shown the file, and the recipient never saw one.

This is where those parts wait. The run observer's reply drains them when it
sends, so a file the agent displayed arrives attached to the reply it was
displayed alongside.

**In Redis, not in this process.** It used to be a per-process dict on the
argument that one run is one asyncio task in one worker. That holds for the
native harness and not for an Agent Host: a remote harness calls its tools over
MCP, those calls execute in whichever API replica holds the host's link, and the
observer that sends the reply runs in a worker. The file was held in one
process and looked for in another, so the reply went out without it while the
tool had told the model it was attached.

Nothing is drained until the reply that carries it has gone out. ``held`` reads
without removing, ``release`` removes exactly what a successful send carried --
so a send that failed leaves the files for the next attempt instead of losing
them, and a path held while the send was in flight is not released with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

# A run that shows a hundred files is a runaway, and the reply would be refused
# by the provider anyway. Bound it rather than letting the list grow.
_MAX_PENDING_PATHS = 20

# The entry only has to outlive one run. An observer that never fires (the
# worker died) would otherwise leave it behind, so it expires on its own; six
# hours is longer than any run is allowed to take.
_TTL_SECONDS = 6 * 60 * 60

# Two ``display_resource`` calls in one turn run concurrently, and the list is
# read, changed and written back. The lock is what stops the second write
# erasing the first, which would drop an attachment without a word.
_LOCK_TTL_SECONDS = 5
_LOCK_ATTEMPTS = 40
_LOCK_RETRY_SECONDS = 0.05

_cache: RedisJsonCache | None = None


def _get_cache() -> RedisJsonCache:
    global _cache
    if _cache is None or _cache._redis_url != settings.redis_url:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:pending-display-paths",
            ttl_seconds=_TTL_SECONDS,
        )
    return _cache


@asynccontextmanager
async def _locked(cache: RedisJsonCache, conversation_id: UUID) -> AsyncIterator[None]:
    """Serialise changes to one conversation's list, across processes."""
    suffix = f"lock:{conversation_id}"
    acquired = False
    for _ in range(_LOCK_ATTEMPTS):
        acquired = await cache.set_raw_if_absent(
            suffix, "1", ttl_seconds=_LOCK_TTL_SECONDS
        )
        if acquired:
            break
        await asyncio.sleep(_LOCK_RETRY_SECONDS)
    if not acquired:
        # Proceed rather than refuse: the lock expires by itself, so whoever
        # holds it is gone or slow, and an attachment held without it is better
        # than one the model was told could not be.
        logger.warning(
            "agent_surfaces.pending_envelope.lock_not_acquired.degraded",
            conversation_id=str(conversation_id),
        )
    try:
        yield
    finally:
        if acquired:
            await cache.delete(suffix)


async def _read(cache: RedisJsonCache, conversation_id: UUID) -> list[str]:
    stored = await cache.get_json(str(conversation_id))
    if not isinstance(stored, list):
        return []
    return [item for item in stored if isinstance(item, str)]


async def _write(
    cache: RedisJsonCache, conversation_id: UUID, paths: list[str]
) -> None:
    if paths:
        await cache.set_json(str(conversation_id), paths)
    else:
        await cache.delete(str(conversation_id))


async def remember_display_path(conversation_id: UUID, path: str) -> bool:
    """Hold a displayed pod file until the one reply goes out.

    Returns whether it was taken: a duplicate or an overflowing run is declined
    rather than silently dropped, so the caller can tell the model the truth.
    Redis being unreachable is a decline too, and says so in the log.
    """
    if not path:
        return False
    cache = _get_cache()
    try:
        async with _locked(cache, conversation_id):
            pending = await _read(cache, conversation_id)
            if path in pending:
                # Showing the same file twice is one attachment, not two.
                return True
            if len(pending) >= _MAX_PENDING_PATHS:
                logger.warning(
                    "agent_surfaces.pending_envelope.display_paths_overflowed.degraded",
                    conversation_id=str(conversation_id),
                    limit=_MAX_PENDING_PATHS,
                )
                return False
            await _write(cache, conversation_id, [*pending, path])
    except RedisError, OSError, TimeoutError, ValueError:
        logger.warning(
            "agent_surfaces.pending_envelope.hold_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return False
    return True


async def held_display_paths(conversation_id: UUID) -> list[str]:
    """What is waiting for the reply, in the order it was shown. Removes nothing.

    Reading is separate from releasing so that a reply which fails to send does
    not take the files with it.
    """
    try:
        return await _read(_get_cache(), conversation_id)
    except RedisError, OSError, TimeoutError, ValueError:
        # Sent without them rather than not sent: the reply matters more than
        # its attachments, and the log is where the missing ones are explained.
        logger.warning(
            "agent_surfaces.pending_envelope.read_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return []


async def release_display_paths(conversation_id: UUID, sent: list[str]) -> None:
    """Forget the paths a reply that has gone out carried.

    Only those: a file shown while the send was in flight belongs to the next
    reply, and clearing the whole entry would lose it.
    """
    if not sent:
        return
    cache = _get_cache()
    try:
        released = set(sent)
        async with _locked(cache, conversation_id):
            remaining = [
                path
                for path in await _read(cache, conversation_id)
                if path not in released
            ]
            await _write(cache, conversation_id, remaining)
    except RedisError, OSError, TimeoutError, ValueError:
        # The reply is out; the worst this does is attach the same file to the
        # next one, until the run ends and discards the entry.
        logger.warning(
            "agent_surfaces.pending_envelope.release_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )


async def discard_display_paths(conversation_id: UUID) -> None:
    """Forget what was collected -- the run ended without a reply carrying it."""
    try:
        await _get_cache().delete(str(conversation_id))
    except RedisError, OSError, TimeoutError:
        logger.warning(
            "agent_surfaces.pending_envelope.discard_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
