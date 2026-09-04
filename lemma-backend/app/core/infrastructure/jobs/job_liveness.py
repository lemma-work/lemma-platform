"""Per-job liveness, published where another process can read it.

A worker lost to SIGKILL, an OOM kill or a vanished node leaves nothing behind
that says the job it was running is over. streaq's running set is a plain Redis
SET -- `sadd` on start, `srem` on finish, no TTL -- so `job_queue.status()`
answers RUNNING for a job whose process no longer exists, and answers it
forever. Everything downstream that has to decide "is this still going" is left
with the wall clock, and the wall clock has to be generous enough for the
longest legitimate job: that is how a conversation stayed unusable for four
hours behind a worker that had been dead for the first minute of it.

So the task wrapper renews a short-TTL key for as long as the job is actually
executing, and a process that is killed renews nothing. Expiry is the one
liveness signal a SIGKILL cannot forge -- it needs no cooperation from the
thing that died, which is exactly what distinguishes it from every status the
job itself writes.

Deliberately the same three-state shape, and the same vocabulary, as
``app/core/observability/worker_liveness.py``, which answers this question one
level up for the worker process. Two keys, because "no key" has two meanings:

``lemma:job:alive:<job id>``
    Short TTL, renewed every ``REFRESH_INTERVAL_SECONDS``. Present only while
    the job is running somewhere that can still turn its event loop.

``lemma:job:seen:<job id>``
    Long TTL, written once when the job first reports. Present if this job ever
    ran at all.

A job that never reported must not be read as a dead one. Ones that never do:
a job in flight across the deploy that introduced this, a job still queued
behind a backlog, and anything whose keys have outlived a Redis failover. Each
would be killed mid-flight by a reader that treated a missing key as death, so
they read as ``None`` -- "this job never reported" -- and the caller falls back
to whatever it did before.

Unlike the worker's, this ``seen`` mark does not need renewing: a job has a
bounded life. Its task timeout is the ceiling, so one long mark at the start
covers the whole run and the grace a sweep adds after it, and the steady-state
cost stays at one ``SET EX`` per interval per running job.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import suppress
import time
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

if TYPE_CHECKING:  # deferred: streaq_runtime is what registers this
    from streaq import Worker

    from app.core.infrastructure.jobs.streaq_runtime import AppWorkerContext

logger = get_logger(__name__)

_ALIVE_KEY_PREFIX = "lemma:job:alive:"
_SEEN_KEY_PREFIX = "lemma:job:seen:"

#: How often a running job renews its heartbeat.
#:
#: One ``SET EX`` per interval per running job, so a worker saturated at its
#: default concurrency costs a few writes a second in total. That is cheap
#: enough that the number worth tuning is the ratio below, not this one.
REFRESH_INTERVAL_SECONDS = 15.0
#: How stale the alive key may be before it expires. Six renewals' worth, the
#: same ratio the worker heartbeat uses, and the reason it is not tighter: a
#: missed tick is not a dead job. The renewal is an ordinary coroutine on the
#: same loop as the work, so a job that holds the loop through a long stretch
#: of otherwise correct code delays it, and the cost of being wrong here is
#: killing a healthy run mid-answer.
_ALIVE_TTL_SECONDS = 90
#: How long "this job reported at least once" is remembered. Comfortably past
#: the longest task timeout plus the grace a sweep adds, so the mark outlives
#: every job it belongs to, and finite so Redis does not accumulate a key per
#: job ever run.
_SEEN_TTL_SECONDS = 24 * 60 * 60


def job_alive_key(job_id: str) -> str:
    return f"{_ALIVE_KEY_PREFIX}{job_id}"


def job_seen_key(job_id: str) -> str:
    return f"{_SEEN_KEY_PREFIX}{job_id}"


async def publish_job_liveness(
    redis_client: Redis, job_id: str, *, mark_seen: bool = False
) -> None:
    """Refresh ``job_id``'s heartbeat. One tick of :func:`job_liveness_loop`."""
    stamp = str(int(time.time()))
    if mark_seen:
        await redis_client.set(job_seen_key(job_id), stamp, ex=_SEEN_TTL_SECONDS)
    await redis_client.set(job_alive_key(job_id), stamp, ex=_ALIVE_TTL_SECONDS)


async def read_job_liveness(redis_client: Redis, job_id: str) -> str | None:
    """``"ok"``, ``"stalled"``, or ``None`` when this job never reported.

    ``None`` for an unreachable Redis too. A caller acts on ``"stalled"`` by
    failing somebody's work, and a Redis outage must not be the reason it does
    that to every running job at once.
    """
    try:
        alive, seen = await asyncio.gather(
            redis_client.exists(job_alive_key(job_id)),
            redis_client.exists(job_seen_key(job_id)),
        )
    except RedisError, OSError, asyncio.TimeoutError:
        logger.warning(
            "infrastructure.job_liveness.read_failed.degraded",
            job_id=job_id,
            exc_info=True,
        )
        return None
    if alive:
        return "ok"
    return "stalled" if seen else None


async def dead_job_ids(
    job_ids: Iterable[str], *, redis_client: Redis | None = None
) -> set[str]:
    """Which of ``job_ids`` reported once and have stopped reporting.

    The subset a caller may treat as gone. Jobs that never reported are not in
    it: see the module docstring for who those are and why killing them would
    be wrong.
    """
    unique = list(dict.fromkeys(job_ids))
    if not unique:
        return set()
    client = get_redis() if redis_client is None else redis_client
    states = await asyncio.gather(
        *(read_job_liveness(client, job_id) for job_id in unique)
    )
    return {
        job_id
        for job_id, state in zip(unique, states, strict=True)
        if state == "stalled"
    }


async def job_liveness_loop(redis_client: Redis, job_id: str) -> None:
    """Keep ``job_id``'s heartbeat fresh until cancelled.

    Beats before it sleeps, so a job reports as soon as the loop reaches this
    coroutine rather than one interval later. ``mark_seen`` stays set until a
    publish actually lands: a job whose very first tick met a Redis blip would
    otherwise run to completion with no record that it ever reported.
    """
    mark_seen = True
    while True:
        try:
            await publish_job_liveness(redis_client, job_id, mark_seen=mark_seen)
            mark_seen = False
        except RedisError, OSError, asyncio.TimeoutError:
            # Not fatal: the job runs regardless, and a reader that cannot see
            # a heartbeat falls back to the wall clock. Logged because a worker
            # whose heartbeats have been failing all along looks exactly like a
            # healthy one until the day something has to ask.
            logger.warning(
                "infrastructure.job_liveness.publish_failed.degraded",
                job_id=job_id,
                exc_info=True,
            )
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


def register_job_liveness_middleware(worker: Worker[AppWorkerContext]) -> None:
    """Renew a heartbeat around every task ``worker`` runs.

    In the task wrapper rather than in the tasks, so liveness is a property of
    running a job at all: no task can opt out by forgetting, and the long ones
    that most need it are not the only ones that have it.

    The renewal is started rather than awaited so a job that finishes in
    milliseconds does not wait on a Redis round trip to begin -- it simply
    never reports, which is a state readers already handle. Nothing deletes the
    key on the way out: the TTL does that, and a job that has just finished is
    not one anybody is asking about.
    """

    def job_liveness_middleware(call_next):
        async def run(*args, **kwargs):
            job_id = registered.context.task_id
            # Inherited rather than clean: the renewal is part of this job,
            # and anything it has to report should say which job it was.
            renewal = create_inherited_task(
                job_liveness_loop(get_redis(), job_id), name=f"job-liveness:{job_id}"
            )
            try:
                return await call_next(*args, **kwargs)
            finally:
                renewal.cancel()
                with suppress(asyncio.CancelledError):
                    await renewal

        return run

    # As with the observability wrapper: `registered` is what exposes the
    # running task to the closure above, and it is bound before any task runs.
    registered = worker.middleware(job_liveness_middleware)
