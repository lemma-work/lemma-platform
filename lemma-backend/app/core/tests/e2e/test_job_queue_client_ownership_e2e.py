"""A job's deadline must not reach the job queue's connection pool.

The root of the 2026-09-28 development outage. The bulk lane's streaq client was
opened lazily, by the first caller that enqueued to it -- a streaq job, running
under its own ``move_on_after`` timeout. A coredis pool is an anyio task group
anchored to the task that enters it, so the pool lived inside that job's scope.
When the deadline fired, every connection the pool opened afterwards, for any
task in the process, failed with a ``CancelledError`` nobody had sent: the bulk
lane crashed, and the ``datastore-file-events`` reader that enqueued next died.

This reproduces the shape with a real streaq client against a real Redis: open
the bulk client inside a scope whose deadline expires, then use it from another
task hard enough to need new connections.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest

from app.core.infrastructure.jobs.streaq_runtime import ensure_task_lanes_registered
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    create_streaq_client,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

redis_container = e2e_fixtures.redis_container
test_redis_url = e2e_fixtures.test_redis_url

#: A task that really is on the bulk lane, so the lane client under test is the
#: one production opened lazily.
_BULK_TASK = "process_datastore_file_task"
#: Enough concurrent commands that the pool must open connections beyond the
#: first -- which is the moment a poisoned pool fails.
_CONCURRENT_COMMANDS = 40


@pytest.fixture
async def queue(test_redis_url, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "redis_url", test_redis_url)
    # Registered before the job runs. Otherwise the first lane lookup spends
    # longer importing every module than the job's deadline allows, and the
    # client is never opened inside the scope -- which is the one thing this
    # test exists to do.
    ensure_task_lanes_registered()
    job_queue = SharedStreaqJobQueue(create_streaq_client)
    yield job_queue
    await job_queue.disconnect()


async def test_a_job_whose_deadline_fires_leaves_the_bulk_client_working(queue):
    async def a_job_that_times_out() -> None:
        # What streaq wraps every job in. Before the fix this raised the exact
        # error the bulk lane died of in development: "Attempted to exit a
        # cancel scope that isn't the current task's current cancel scope".
        with anyio.move_on_after(1.0):
            await queue._lane_client(_BULK_TASK)
            await anyio.sleep(10)

    await asyncio.create_task(a_job_that_times_out())

    async def a_stream_consumer() -> list[bool]:
        client = await queue._lane_client(_BULK_TASK)
        return await asyncio.gather(
            *(client.redis.ping() for _ in range(_CONCURRENT_COMMANDS))
        )

    replies = await asyncio.wait_for(asyncio.create_task(a_stream_consumer()), 20)

    assert len(replies) == _CONCURRENT_COMMANDS
