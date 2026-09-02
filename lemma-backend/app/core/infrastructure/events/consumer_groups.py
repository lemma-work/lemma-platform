"""Keeping the Redis consumer groups alive for a running worker.

Two passes over the same idempotent XGROUP CREATE, for two different accidents:
one before the broker starts, closing the startup race where a subscriber polls
a group that does not exist yet; one on a timer, so a group lost mid-run --
flush, failover to an un-replicated replica, eviction, trim -- comes back
without anyone restarting the process.

Separate from the worker runtime that starts them because the thing they
maintain is the stream topology, not the job queue, and because the runtime is
already the largest file in the package.
"""

from __future__ import annotations

import asyncio

from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_subscriber import ensure_consumer_groups
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

logger = get_logger(__name__)


async def ensure_consumer_groups_once() -> None:
    """Create every registered Redis consumer group once, before broker start.

    Closes the broker-start race where a subscriber polls a not-yet-created
    group and gets NOGROUP, which costs it a supervisor restart per attempt
    until the group exists. Idempotent (BUSYGROUP is a no-op) and never raises
    — group plumbing must not block worker startup.
    """
    # FastStream and streaq speak raw bytes, so this shares the
    # decode_responses=False pool rather than the application one.
    client = get_redis(decode_responses=False, blocking=True)
    try:
        await ensure_consumer_groups(client, warn_on_create=False)
    except Exception:  # pragma: no cover - defensive
        # A worker that starts without its groups consumes nothing until the
        # reconcile loop's first tick, and on a Redis that is down it consumes
        # nothing at all. That is not a debug detail.
        logger.error(
            "infrastructure.consumer_groups.initial_ensure.failed",
            exc_info=True,
        )


async def reconcile_consumer_groups_once(redis_client) -> None:
    """One reconcile tick. Reports its own failure; never raises."""
    try:
        await ensure_consumer_groups(redis_client)
    except Exception:
        # The loop is the only thing that revives a lost group, so a tick that
        # cannot run means delivery stays stopped for as long as it lasts.
        logger.error(
            "infrastructure.consumer_groups.reconcile.failed",
            exc_info=True,
        )


async def consumer_group_reconcile_loop() -> None:
    """Periodically re-ensure Redis consumer groups exist.

    Self-heals the FastStream supervisor retry-storm: if a consumer group is lost
    (flush / failover / eviction / trim), the subscriber's consume task raises
    NOGROUP, the supervisor restarts it immediately, and it spins there. FastStream
    logs "Stopping subscriber — restart the application to recreate the group",
    but the ``TaskCallbackSupervisor`` attached by ``TasksMixin.add_task`` retries
    the task regardless of that message, so recreating the group does let the next
    attempt succeed and the subscriber resume — no manual restart. Cheap (one
    Redis connection, a handful of idempotent XGROUP CREATE calls per tick).
    """
    interval = event_transport_settings.consumer_group_reconcile_interval_seconds
    client = get_redis(decode_responses=False, blocking=True)
    try:
        while True:
            await reconcile_consumer_groups_once(client)
            await asyncio.sleep(interval)
    finally:
        await client.aclose()
