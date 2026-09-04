"""Forgetting cron schedules for tasks the worker no longer knows how to run.

streaq keeps a cron's schedule in Redis, not in the code: the registry hash and
the schedule sorted set outlive the deployment that wrote them. So deleting a
cron does not stop it. ``schedule_delayed_tasks`` keeps finding the name due,
keeps enqueuing it, and the consumer keeps dropping it with "skipped, missing
function" -- forever, at the cron's own interval.

``resume_interrupted_agent_runs`` was removed months before this was written and
was still being enqueued every five minutes: 294 error-level lines a day, which
is what real worker errors were being read against. Nothing was broken by it;
everything was harder to see.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from app.core.log.log import get_logger

logger = get_logger(__name__)


class CronKeyedWorker(Protocol):
    """The part of a streaq ``Worker`` this needs, and no more.

    Notably *not* its ``redis``. A streaq ``Worker`` only exposes one once its
    async context manager has entered, and this sweep has to happen before the
    worker starts consuming -- so it is handed a client instead. Reading
    ``worker.redis`` here raised ``StreaqError`` and took the whole worker
    process down on startup, which the e2e suite caught immediately.
    """

    registry: Mapping[str, object]
    queue_name: str
    cron_schedule_key: str
    cron_registry_key: str
    cron_data_key: str


def _redis_failures() -> tuple[type[BaseException], ...]:
    """What talking to Redis can fail with.

    Named rather than caught as ``Exception``: housekeeping must not stop a
    worker starting, but it also must not swallow a bug in this module and
    report it as Redis being unwell.
    """
    from redis.exceptions import RedisError

    return (RedisError, OSError, TimeoutError)


async def prune_orphaned_crons(worker: CronKeyedWorker, *, redis) -> list[str]:
    """Delete every cron schedule with no registered function behind it.

    Swept per lane, against that lane's own keys and its own registry, because
    the keys are namespaced by queue name. A rolling deploy where an older
    worker still serves a cron this one has dropped would have that cron pruned
    from under it -- which is survivable, since the older worker re-registers it
    on its next start, and the alternative is a schedule nothing ever cleans.
    """
    known = set(worker.registry)
    if not known:
        # A worker that registered nothing has not proved that anything is
        # orphaned -- it has proved that its own handlers were never imported.
        # Sweeping on that evidence deletes every cron in the queue, which is
        # the one outcome worse than a stale schedule.
        logger.warning(
            "worker.crons.prune_skipped_empty_registry.degraded",
            queue=worker.queue_name,
        )
        return []
    scheduled = await redis.zrange(worker.cron_schedule_key, 0, -1)
    registered = await redis.hkeys(worker.cron_registry_key)
    orphaned = sorted({str(name) for name in (*scheduled, *registered)} - known)
    if not orphaned:
        return []
    # `await pipe.execute()`, not just the context manager. redis-py queues the
    # commands and *discards* them on exit unless they are executed -- so
    # without this the sweep logged what it had deleted and deleted nothing,
    # which is exactly what a live run showed. streaq talks to Redis through
    # coredis, whose pipeline executes on exit and whose commands take a list
    # rather than varargs; this module is handed the application's redis-py
    # client, and the two APIs are not interchangeable.
    async with redis.pipeline(transaction=False) as pipe:
        pipe.zrem(worker.cron_schedule_key, *orphaned)
        pipe.hdel(worker.cron_registry_key, *orphaned)
        pipe.delete(*(worker.cron_data_key + name for name in orphaned))
        await pipe.execute()
    logger.info(
        "worker.crons.pruned",
        queue=worker.queue_name,
        tasks=",".join(orphaned),
    )
    return orphaned


async def prune_orphaned_crons_safely(worker: CronKeyedWorker, *, redis) -> None:
    """Housekeeping must never be the reason a worker fails to start."""
    try:
        await prune_orphaned_crons(worker, redis=redis)
    except _redis_failures():
        logger.warning(
            "worker.crons.prune_failed.degraded",
            queue=worker.queue_name,
            exc_info=True,
        )
