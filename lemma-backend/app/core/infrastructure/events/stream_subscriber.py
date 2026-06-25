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


async def ensure_consumer_groups(
    redis_client, *, warn_on_create: bool = True, only_stream: str | None = None
) -> int:
    """Idempotently (re)create every registered Redis consumer group.

    Returns the number of groups (re)created. Two cases need this:

    1. **Startup race.** Multiple subscribers can share one stream (e.g. both the
       workflow and surface subscribers consume ``schedule_events``). At
       ``broker.start`` FastStream races to create each group, and a subscriber
       that issues XREADGROUP before its group exists gets NOGROUP and *stops
       permanently* ("restart the application to recreate the group") — the
       reconcile loop cannot revive a stopped subscriber. Calling this once
       before ``broker.start`` pre-creates every group so no subscriber races.
       Pass ``warn_on_create=False`` there: creating a group on a fresh (or
       flushed) Redis is expected, not an anomaly.
    2. **Mid-run loss.** If a group is later lost — Redis flush, failover to an
       un-replicated replica, key eviction, or stream trim — recreating it on a
       short interval lets a retrying subscriber resume without a restart.

    Groups are created at ``$`` (new messages only): after a data-loss event the
    old entries are gone anyway, and this avoids reprocessing a whole surviving
    stream. Never raises — group plumbing must not crash the worker.
    """
    created = 0
    for stream, group in registered_stream_groups():
        if only_stream is not None and stream != only_stream:
            continue
        try:
            await redis_client.xgroup_create(
                name=stream, groupname=group, id="$", mkstream=True
            )
            created += 1
            if warn_on_create:
                logger.warning(
                    "Recreated missing Redis consumer group '%s' on stream '%s'",
                    group,
                    stream,
                )
            else:
                logger.debug(
                    "Created Redis consumer group '%s' on stream '%s'",
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


async def ensure_named_groups(
    redis_client, stream: str, groups, *, warn_on_create: bool = False
) -> int:
    """Idempotently create explicitly-named consumer groups on a stream.

    Unlike ``ensure_consumer_groups`` (which reads the per-process subscriber
    registry), this takes the group names directly — so a PUBLISHER process that
    never imports the consuming subscribers (the scheduler pod, the API pod) can
    still guarantee a consumer's group exists before XADD. Created at ``$`` /
    mkstream; BUSYGROUP keeps the existing group and its position. Never raises.
    """
    created = 0
    for group in groups:
        try:
            await redis_client.xgroup_create(
                name=stream, groupname=group, id="$", mkstream=True
            )
            created += 1
            if warn_on_create:
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
