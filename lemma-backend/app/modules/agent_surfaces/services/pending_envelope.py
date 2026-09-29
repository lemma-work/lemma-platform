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

**Held per run, not per conversation.** A file belongs to the run that showed it.
Keyed by conversation alone, a run whose final reply failed to send left its
files for whichever reply came next -- another turn's, or an apology's -- so a
later turn could mail a person files it had never shown. Keyed by
(conversation, run), only that run's own reply ever reads them.

**No read-modify-write.** Each run's files are an insertion-ordered set in
Redis, so two ``display_resource`` calls in one turn add concurrently and every
path lands once; there is no lock to time out of.

Nothing is drained until the reply that carries it has gone out. ``held`` reads
without removing, ``release`` removes exactly what a successful send carried --
so a send that failed leaves the files for the next attempt instead of losing
them, and a path held while the send was in flight is not released with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

# A run that shows a hundred files is a runaway, and the reply would be refused
# by the provider anyway. Bound it rather than letting the list grow.
_MAX_PENDING_PATHS = 20

# The entry only has to outlive one run and the recovery of its reply. It is
# discarded when the run ends with nothing left to recover, and expires on its
# own otherwise (a worker that died, a final send that never succeeded); six
# hours is longer than any run is allowed to take.
_TTL_SECONDS = 6 * 60 * 60

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


@dataclass(frozen=True)
class RunFiles:
    """Whose held files a send may carry: those of one run of one conversation.

    ``agent_run_id`` is None for a context that has no run (a tool reached over a
    bridge with none active); those files share one slot, which is still not
    any other run's.
    """

    agent_run_id: UUID | None


def _slot(conversation_id: UUID, run: RunFiles) -> str:
    return f"{conversation_id}:{run.agent_run_id or 'no-run'}"


async def remember_display_path(
    conversation_id: UUID, run: RunFiles, path: str
) -> bool:
    """Hold a displayed pod file until the run's one reply goes out.

    Returns whether it was taken: an overflowing run is declined rather than
    silently dropped, so the caller can tell the model the truth. A file shown
    twice is one attachment and is reported as taken. Redis being unreachable is
    a decline too, and says so in the log.
    """
    if not path:
        return False
    try:
        taken = await _get_cache().ordered_set_add(
            _slot(conversation_id, run), path, limit=_MAX_PENDING_PATHS
        )
    except RedisError, OSError, TimeoutError, ValueError:
        logger.warning(
            "agent_surfaces.pending_envelope.hold_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return False
    if not taken:
        logger.warning(
            "agent_surfaces.pending_envelope.display_paths_overflowed.degraded",
            conversation_id=str(conversation_id),
            limit=_MAX_PENDING_PATHS,
        )
    return taken


async def held_display_paths(conversation_id: UUID, run: RunFiles) -> list[str]:
    """What this run has waiting for its reply, in the order shown. Removes nothing.

    Reading is separate from releasing so that a reply which fails to send does
    not take the files with it.
    """
    try:
        return await _get_cache().ordered_set_members(_slot(conversation_id, run))
    except RedisError, OSError, TimeoutError, ValueError:
        # Sent without them rather than not sent: the reply matters more than
        # its attachments, and the log is where the missing ones are explained.
        logger.warning(
            "agent_surfaces.pending_envelope.read_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return []


async def release_display_paths(
    conversation_id: UUID, run: RunFiles, sent: list[str]
) -> None:
    """Forget the paths a reply that has gone out carried.

    Only those: a file shown while the send was in flight belongs to the next
    reply, and clearing the whole entry would lose it.
    """
    if not sent:
        return
    try:
        await _get_cache().ordered_set_remove(_slot(conversation_id, run), sent)
    except RedisError, OSError, TimeoutError, ValueError:
        # The reply is out; the worst this does is attach the same file to the
        # run's next reply, until the entry is discarded or expires.
        logger.warning(
            "agent_surfaces.pending_envelope.release_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )


async def discard_display_paths(conversation_id: UUID, run: RunFiles) -> None:
    """Forget a run's held files -- the run is over and no reply will carry them."""
    try:
        await _get_cache().ordered_set_clear(_slot(conversation_id, run))
    except RedisError, OSError, TimeoutError:
        logger.warning(
            "agent_surfaces.pending_envelope.discard_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
