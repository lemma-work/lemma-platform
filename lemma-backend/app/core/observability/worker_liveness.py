"""Worker liveness, published where another process can read it.

The loop watchdog already writes a heartbeat file every tick, and that file is
the right signal for a liveness *probe* -- something running beside the worker,
on the same filesystem, that can restart it. It is the wrong signal for
readiness, because the process that has to answer `/health/ready` is usually not
the process with that file: `python -m app.worker` runs beside `uvicorn` in the
container stack and in the documented Kubernetes layout, and only the
single-process desktop build has both in one place.

So the watchdog also refreshes two Redis keys, and the API reads them:

``lemma:worker:alive``
    Short TTL. Present only while a worker has ticked recently, so a wedged loop
    -- which cannot refresh it any more than it can refresh the file -- lets it
    expire.

``lemma:worker:seen``
    Long TTL. Present if a worker has run against this Redis at all recently.

Both are needed because "no key" has two meanings. A deployment that runs no
worker at all -- an API-only test process, a developer running `uvicorn` alone --
must not be reported unready for a component it never had; a deployment whose
worker *was* there and is now silent must. ``seen`` is what tells those apart,
and its window is the bound on the answer: a worker gone for longer than that
stops being reported, because at that point nothing in this Redis remembers it
and readiness would be guessing.

Deliberately not a per-replica registry. The question readiness asks is "is
there a worker able to take this work", not "how many are there" -- so every
replica refreshes the same two keys and any one of them alive is enough.
"""

from __future__ import annotations

import asyncio
import time

from redis.exceptions import RedisError

from app.core.log.log import get_logger

logger = get_logger(__name__)

WORKER_ALIVE_KEY = "lemma:worker:alive"
WORKER_SEEN_KEY = "lemma:worker:seen"

#: How stale the alive key may be before it expires. Matches the freshness
#: window the readiness endpoint applies to the heartbeat file, so the two
#: topologies answer on the same timescale.
_ALIVE_TTL_SECONDS = 60
#: How long "a worker has run here" is remembered once none is answering. Long
#: enough that a rolling deploy, a crash-loop or a node replacement stays inside
#: it, and finite so an install that deliberately stops running a worker is not
#: held unready forever by a key nothing will ever refresh.
_SEEN_TTL_SECONDS = 24 * 60 * 60
#: Well inside the alive TTL, so an ordinary missed tick is not a dead worker.
REFRESH_INTERVAL_SECONDS = 10.0


async def publish_worker_liveness(redis_client) -> None:
    """Refresh both keys. One tick of :func:`worker_liveness_loop`."""
    stamp = str(int(time.time()))
    await redis_client.set(WORKER_ALIVE_KEY, stamp, ex=_ALIVE_TTL_SECONDS)
    await redis_client.set(WORKER_SEEN_KEY, stamp, ex=_SEEN_TTL_SECONDS)


async def read_worker_liveness(redis_client) -> str | None:
    """``"ok"``, ``"stalled"``, or ``None`` when the question does not apply.

    ``None`` for an unreachable Redis too: readiness already checks Redis on its
    own and reports it by name, and answering "the worker is stalled" for a
    Redis outage would point the operator at the wrong component.
    """
    try:
        alive, seen = await asyncio.gather(
            redis_client.exists(WORKER_ALIVE_KEY),
            redis_client.exists(WORKER_SEEN_KEY),
        )
    except RedisError, OSError, asyncio.TimeoutError:
        logger.warning(
            "observability.worker_liveness.read_failed.degraded", exc_info=True
        )
        return None
    if alive:
        return "ok"
    return "stalled" if seen else None


#: How stale the worker heartbeat file may be before readiness calls it stalled.
#:
#: The watchdog refreshes it every `loop_lag_watchdog_interval_seconds` (0.5s by
#: default), so a minute is roughly two orders of magnitude of headroom --
#: generous enough that a busy machine or a long GC pause is never mistaken for
#: a dead worker, and short enough that a person does not sit in front of a
#: spinner for hours, which is what happened.
HEARTBEAT_MAX_AGE_SECONDS = 60.0


def heartbeat_file_state(path: str | None) -> str | None:
    """Whether the worker sharing this filesystem is still ticking, or None.

    The gap this closes is one a desktop install sat in for hours. The heartbeat
    file exists so a liveness probe can restart a wedged worker -- and on
    desktop nothing read it. `/health/ready` answered 200 on the strength of the
    database and Redis while the worker had been dead since a lifespan teardown
    two hours earlier, so locald's health gate saw a healthy backend, never
    restarted it, and every agent run queued behind a worker that was not there.
    The UI showed "thinking" and no log said otherwise.

    A missing file is not a stalled worker: it is a process that has not written
    one yet, which is every start before the first tick.
    """
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            written = float(handle.read().strip())
    except OSError, ValueError:
        return None
    return "ok" if time.time() - written <= HEARTBEAT_MAX_AGE_SECONDS else "stalled"


async def worker_readiness_state(
    *,
    embedded: bool,
    heartbeat_path: str | None = None,
    redis_client=None,
    timeout_seconds: float = 1.0,
) -> str | None:
    """The worker component of `/health/ready`, for either topology.

    An embedded worker is in this process, so its heartbeat file is on this
    filesystem and is the cheaper, more direct answer -- no network, no shared
    state. Anywhere else the worker is another process, possibly another
    machine, and Redis is the only place both can see.

    Bounded here rather than at the endpoint, and never raising, so readiness
    keeps its own deadline and a slow answer about the worker cannot become a
    slow answer about the database.
    """
    if embedded:
        from app.core.config import settings

        return heartbeat_file_state(
            heartbeat_path
            if heartbeat_path is not None
            else settings.worker_heartbeat_path
        )
    if redis_client is None:
        from app.core.infrastructure.redis.client import get_redis

        redis_client = get_redis()
    try:
        return await asyncio.wait_for(
            read_worker_liveness(redis_client), timeout=timeout_seconds
        )
    except TimeoutError:
        # Not a stalled worker: a Redis that did not answer in the budget. The
        # readiness Redis check reports that on its own.
        logger.warning(
            "observability.worker_liveness.read_timed_out.degraded",
            timeout_seconds=timeout_seconds,
        )
        return None


async def worker_liveness_loop(redis_client) -> None:
    """Background task: keep the two keys fresh for as long as the loop turns."""
    while True:
        try:
            await publish_worker_liveness(redis_client)
        except RedisError, OSError, asyncio.TimeoutError:
            # Not fatal -- the heartbeat file and the `worker.heartbeat` event
            # still report this process. What is lost is the API's ability to
            # answer for it, which is exactly the gap this loop exists to close,
            # so it is not a debug detail.
            logger.warning(
                "observability.worker_liveness.publish_failed.degraded", exc_info=True
            )
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
