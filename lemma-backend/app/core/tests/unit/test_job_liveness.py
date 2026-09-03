"""Whether a job that was killed can be told from one that is still working.

streaq's running set is a plain Redis SET with no expiry, so a worker lost to
SIGKILL leaves every job it was executing reported as RUNNING for good. Nothing
the job writes can close that gap -- a killed process writes nothing -- so the
signal has to be one that lapses on its own, and these pin the three answers a
reader has to tell apart:

* the job renewed its key recently -> ok, leave it alone;
* the job reported once and has stopped -> stalled, its worker is gone;
* the job never reported -> the question does not apply, because a job queued
  behind a backlog and a job from before this shipped look exactly like a dead
  one to anybody who reads a missing key as death.

Against `fakeredis` rather than a hand-written stand-in: the thing under test
is what Redis does to a key nobody renews, and a fake with no expiry clock
would certify only the half that already worked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fakeredis import aioredis as fake_aioredis

from app.core.infrastructure.jobs import job_liveness

_JOB = "agent-run:9d4a1c0e-0000-4000-8000-0000000000ab"
_OTHER = "agent-run:9d4a1c0e-0000-4000-8000-0000000000cd"


@pytest.fixture
def redis() -> fake_aioredis.FakeRedis:
    return fake_aioredis.FakeRedis(decode_responses=True)


async def _expire_now(redis, key: str) -> None:
    """What the TTL does when nothing renews the key, without the wait."""
    await redis.pexpire(key, 1)
    await asyncio.sleep(0.05)


async def test_a_job_that_just_reported_reads_as_ok(redis) -> None:
    await job_liveness.publish_job_liveness(redis, _JOB, mark_seen=True)

    assert await job_liveness.read_job_liveness(redis, _JOB) == "ok"


async def test_a_job_that_stopped_reporting_reads_as_stalled(redis) -> None:
    """The SIGKILL case: nothing renewed the key, so the key went away."""
    await job_liveness.publish_job_liveness(redis, _JOB, mark_seen=True)

    await _expire_now(redis, job_liveness.job_alive_key(_JOB))

    assert await job_liveness.read_job_liveness(redis, _JOB) == "stalled"


async def test_a_job_that_never_reported_is_not_a_dead_job(redis) -> None:
    assert await job_liveness.read_job_liveness(redis, _JOB) is None


async def test_liveness_expires_long_before_presence(redis) -> None:
    """The short key answers "now"; the long one answers "ever"."""
    await job_liveness.publish_job_liveness(redis, _JOB, mark_seen=True)

    assert await redis.ttl(job_liveness.job_alive_key(_JOB)) < await redis.ttl(
        job_liveness.job_seen_key(_JOB)
    )


async def test_renewal_refreshes_liveness_without_remarking_presence(redis) -> None:
    """Steady state is one `SET EX`: the seen mark is written once, at the start.

    A job's life is bounded by its task timeout, so unlike the worker's, this
    mark does not have to be kept alive -- and paying for it every interval,
    for every running job, is the cost this exists to keep small.
    """
    await job_liveness.publish_job_liveness(redis, _JOB, mark_seen=True)
    await redis.pexpire(job_liveness.job_seen_key(_JOB), 1)
    await asyncio.sleep(0.05)

    await job_liveness.publish_job_liveness(redis, _JOB)

    assert await redis.exists(job_liveness.job_seen_key(_JOB)) == 0
    assert await redis.exists(job_liveness.job_alive_key(_JOB)) == 1


async def test_dead_job_ids_names_only_the_ones_that_went_silent(redis) -> None:
    await job_liveness.publish_job_liveness(redis, _JOB, mark_seen=True)
    await job_liveness.publish_job_liveness(redis, _OTHER, mark_seen=True)
    await _expire_now(redis, job_liveness.job_alive_key(_JOB))

    dead = await job_liveness.dead_job_ids(
        [_JOB, _OTHER, "job-that-never-ran"], redis_client=redis
    )

    assert dead == {_JOB}


async def test_dead_job_ids_asks_nothing_of_redis_for_an_empty_sweep() -> None:
    """A sweep with no candidates must not reach for a client at all."""
    assert await job_liveness.dead_job_ids([]) == set()


async def test_the_renewal_loop_keeps_a_job_alive_until_it_is_cancelled(
    redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(job_liveness, "REFRESH_INTERVAL_SECONDS", 0.01)
    loop = asyncio.create_task(job_liveness.job_liveness_loop(redis, _JOB))
    await asyncio.sleep(0.05)

    assert await job_liveness.read_job_liveness(redis, _JOB) == "ok"

    loop.cancel()
    await _expire_now(redis, job_liveness.job_alive_key(_JOB))

    assert await job_liveness.read_job_liveness(redis, _JOB) == "stalled"


async def test_a_redis_that_will_not_answer_is_never_reported_as_death(
    redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller acts on "stalled" by failing somebody's work."""
    await job_liveness.publish_job_liveness(redis, _JOB, mark_seen=True)

    async def _refuse(*_args: object, **_kwargs: object) -> int:
        raise OSError("connection reset")

    monkeypatch.setattr(redis, "exists", _refuse)

    assert await job_liveness.read_job_liveness(redis, _JOB) is None
    assert await job_liveness.dead_job_ids([_JOB], redis_client=redis) == set()


class _Worker:
    """streaq's registration shape: `middleware()` takes the factory and returns
    the object through which the wrapper reaches the running task."""

    def __init__(self, task_id: str) -> None:
        self.registered = SimpleNamespace(context=SimpleNamespace(task_id=task_id))
        self.factory = None

    def middleware(self, factory):
        self.factory = factory
        return self.registered


async def test_the_task_wrapper_reports_for_the_life_of_the_job(
    redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What makes this liveness and not bookkeeping: it is renewed by the
    wrapper every task goes through, and it stops when the task does."""
    monkeypatch.setattr(job_liveness, "get_redis", lambda **_kwargs: redis)
    monkeypatch.setattr(job_liveness, "REFRESH_INTERVAL_SECONDS", 0.01)
    worker = _Worker(_JOB)
    job_liveness.register_job_liveness_middleware(worker)
    reported: dict[str, str | None] = {}

    async def task_body() -> str:
        await asyncio.sleep(0.05)
        reported["during"] = await job_liveness.read_job_liveness(redis, _JOB)
        return "finished"

    assert await worker.factory(task_body)() == "finished"
    assert reported["during"] == "ok"

    # Nothing renews it once the task is over, so it lapses like a dead job's.
    await _expire_now(redis, job_liveness.job_alive_key(_JOB))

    assert await job_liveness.read_job_liveness(redis, _JOB) == "stalled"
