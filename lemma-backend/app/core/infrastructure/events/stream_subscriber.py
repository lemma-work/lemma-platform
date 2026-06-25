from __future__ import annotations

from faststream.redis import StreamSub

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Every grouped stream subscriber registers its (stream, group) here at import
# time (the decorators call redis_stream_sub). The worker uses this to keep the
# Redis consumer groups alive — see ensure_consumer_groups below.
_REGISTERED_STREAM_GROUPS: set[tuple[str, str]] = set()


def redis_stream_sub(
    stream: str,
    *,
    group: str | None = None,
    consumer: str | None = None,
) -> StreamSub:
    """Create a Redis Stream subscriber with a shared polling interval."""
    if group:
        _REGISTERED_STREAM_GROUPS.add((stream, group))
    return StreamSub(
        stream,
        group=group,
        consumer=consumer,
        polling_interval=settings.redis_stream_polling_interval_ms,
    )


def registered_stream_groups() -> set[tuple[str, str]]:
    """All (stream, group) pairs declared by grouped stream subscribers."""
    return set(_REGISTERED_STREAM_GROUPS)


async def ensure_consumer_groups(redis_client) -> int:
    """Idempotently (re)create every registered Redis consumer group.

    Returns the number of groups (re)created. FastStream creates each group on
    subscriber start, but if a group is later lost — Redis flush, failover to an
    un-replicated replica, key eviction, or stream trim — the subscriber's
    consume loop fails with NOGROUP and FastStream's supervisor retries it with
    no backoff, pinning the worker at 100% CPU forever (it never recreates the
    group). Running this on a short interval recreates the lost group so the next
    retry succeeds and the subscriber resumes — self-healing without a restart.

    Groups are created at ``$`` (new messages only): after a data-loss event the
    old entries are gone anyway, and this avoids reprocessing a whole surviving
    stream. Never raises — group plumbing must not crash the worker.
    """
    created = 0
    for stream, group in registered_stream_groups():
        try:
            await redis_client.xgroup_create(
                name=stream, groupname=group, id="$", mkstream=True
            )
            created += 1
            logger.warning(
                "Recreated missing Redis consumer group '%s' on stream '%s'",
                group,
                stream,
            )
        except Exception as exc:  # BUSYGROUP (already exists) is the happy path
            if "BUSYGROUP" not in str(exc):
                logger.warning(
                    "Failed ensuring consumer group '%s' on stream '%s': %s",
                    group,
                    stream,
                    exc,
                )
    return created
