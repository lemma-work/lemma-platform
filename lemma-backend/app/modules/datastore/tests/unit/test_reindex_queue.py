from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.reindex_queue import (
    RedisDatastoreReindexQueue,
)


@pytest.mark.asyncio
async def test_enqueue_returns_true_when_job_newly_queued():
    """streaq returns a truthy task object when the task is newly queued."""
    job_queue = AsyncMock()
    job_queue.enqueue.return_value = object()
    queue = RedisDatastoreReindexQueue(job_queue=job_queue)
    queue._read_admission_state = AsyncMock(return_value=(True, 0))

    file_id = uuid4()
    pod_id = uuid4()
    queued = await queue.enqueue(
        file_id=file_id,
        pod_id=pod_id,
        metadata={"source": "ui"},
        defer_until=None,
    )

    assert queued is True
    job_queue.enqueue.assert_awaited_once_with(
        "process_datastore_file_task",
        _job_id=f"datastore_file:{file_id}",
        _defer_until=None,
        file_id=str(file_id),
        pod_id=str(pod_id),
        metadata={"source": "ui"},
    )


@pytest.mark.asyncio
async def test_enqueue_returns_false_when_job_already_queued():
    """streaq returns None when a task with the same task ID is already pending."""
    job_queue = AsyncMock()
    job_queue.enqueue.return_value = None
    queue = RedisDatastoreReindexQueue(job_queue=job_queue)
    queue._read_admission_state = AsyncMock(return_value=(True, 0))

    file_id = uuid4()
    defer_until = datetime(2026, 4, 9, 14, 35, tzinfo=timezone.utc)
    queued = await queue.enqueue(
        file_id=file_id,
        pod_id=uuid4(),
        metadata=None,
        defer_until=defer_until,
    )

    assert queued is False
    job_queue.enqueue.assert_awaited_once_with(
        "process_datastore_file_task",
        _job_id=f"datastore_file:{file_id}:{int(defer_until.timestamp())}",
        _defer_until=defer_until,
        file_id=str(file_id),
        pod_id=str(job_queue.enqueue.await_args.kwargs["pod_id"]),
        metadata={},
    )


@pytest.mark.asyncio
async def test_enqueue_returns_false_when_file_is_not_pending():
    job_queue = AsyncMock()
    queue = RedisDatastoreReindexQueue(job_queue=job_queue)
    queue._read_admission_state = AsyncMock(return_value=(False, 0))

    queued = await queue.enqueue(
        file_id=uuid4(),
        pod_id=uuid4(),
        metadata=None,
        defer_until=None,
    )

    assert queued is False
    job_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_defers_to_the_dispatcher_once_a_pod_is_saturated():
    """Past its allowance a pod stops queueing — the PENDING row is the backlog.

    Nothing is dropped: leaving it PENDING is exactly what lets the fair
    dispatcher hand it out round-robin instead of one tenant filling the queue.
    """
    job_queue = AsyncMock()
    queue = RedisDatastoreReindexQueue(job_queue=job_queue)
    limit = datastore_settings.datastore_per_pod_max_inflight
    queue._read_admission_state = AsyncMock(return_value=(True, limit))

    queued = await queue.enqueue(
        file_id=uuid4(), pod_id=uuid4(), metadata=None, defer_until=None
    )

    assert queued is False
    job_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_bypasses_the_gate_that_deferred_the_work_to_it():
    """Re-applying the gate here would deadlock the backlog — nothing would drain."""
    job_queue = AsyncMock()
    job_queue.enqueue.return_value = object()
    queue = RedisDatastoreReindexQueue(job_queue=job_queue)
    queue._read_admission_state = AsyncMock(return_value=(True, 10_000))

    queued = await queue.enqueue(
        file_id=uuid4(),
        pod_id=uuid4(),
        metadata=None,
        defer_until=None,
        bypass_admission=True,
    )

    assert queued is True
    job_queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_pod_under_its_allowance_still_enqueues_immediately():
    """The common case — a single upload — must not wait for the dispatcher."""
    job_queue = AsyncMock()
    job_queue.enqueue.return_value = object()
    queue = RedisDatastoreReindexQueue(job_queue=job_queue)
    queue._read_admission_state = AsyncMock(return_value=(True, 0))

    queued = await queue.enqueue(
        file_id=uuid4(), pod_id=uuid4(), metadata=None, defer_until=None
    )

    assert queued is True
