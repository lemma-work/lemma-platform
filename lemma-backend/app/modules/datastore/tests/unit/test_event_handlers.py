from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.infrastructure.jobs.streaq_runtime import (
    TASK_LANES,
    Lane,
    lane_for_task,
    lane_queue_name,
)
from app.modules.datastore.events import handlers
from app.modules.datastore.domain.events import (
    DatastoreFileCreatedEvent,
    DatastoreFileUpdatedEvent,
)
from app.modules.test_support.fakes import PassthroughEventInbox


def test_document_processing_runs_on_the_bulk_lane():
    """Ingestion must not share a queue with latency-sensitive work.

    Document extraction used to be throttled by a semaphore *inside* the task,
    which meant a queued extraction still occupied a worker slot while waiting —
    so a bulk upload could starve agent runs and surface messages. The bound is
    now the bulk lane's own worker concurrency, and the only thing that keeps
    that true is this task being registered on the bulk lane.
    """
    assert lane_for_task("process_datastore_file_task") is Lane.BULK
    # Its companions are background work too; leaving any of them interactive
    # would reopen the same starvation path.
    assert lane_for_task("cleanup_deleted_datastore_paths") is Lane.BULK
    assert lane_for_task("recover_stuck_processing_files") is Lane.BULK
    # Separate Redis queues are what make a deep bulk backlog invisible to the
    # interactive lane.
    assert lane_queue_name(Lane.BULK) != lane_queue_name(Lane.INTERACTIVE)


def test_interactive_work_stays_on_the_interactive_lane():
    """The things a human is waiting on keep their own queue and budget."""
    # Import every module's tasks so the registry is fully populated —
    # lane_for_task() falls back to INTERACTIVE for unknown names, so without
    # this the assertions below would pass even if nothing were registered.
    import app.events  # noqa: F401

    for task_name in (
        "process_agent_run",
        "process_surface_message",
        "resume_workflow_run_for_agent",
        "process_function_run",
    ):
        assert task_name in TASK_LANES, f"{task_name} is not registered on any lane"
        assert lane_for_task(task_name) is Lane.INTERACTIVE


@pytest.mark.asyncio
async def test_process_datastore_file_task_no_longer_serializes_in_process(
    monkeypatch,
):
    """The in-task semaphore is gone: the task itself must not self-throttle.

    Concurrency is the bulk lane's; if this task still gated internally we would
    be limiting twice and holding slots while blocked.
    """
    processed: list[str] = []

    class _FakeProcessingService:
        current = 0
        max_seen = 0

        def __init__(self, pod_id, *, uow_factory):
            self.search_service = SimpleNamespace(engine=None)

        async def process_file_async(self, file_id, metadata):
            del metadata
            type(self).current += 1
            type(self).max_seen = max(type(self).max_seen, type(self).current)
            processed.append(str(file_id))
            await asyncio.sleep(0.01)
            type(self).current -= 1

    composition = SimpleNamespace(
        build_processing_service=lambda pod_id, *, uow_factory: _FakeProcessingService(
            pod_id, uow_factory=uow_factory
        )
    )
    monkeypatch.setattr(handlers, "get_datastore_composition", lambda: composition)

    pod_id = str(uuid4())
    await asyncio.gather(
        *[
            handlers.process_datastore_file_task(
                None,
                file_id=str(uuid4()),
                pod_id=pod_id,
                metadata={"index": index},
            )
            for index in range(50)
        ]
    )

    assert len(processed) == 50
    # All 50 ran concurrently — nothing inside the task capped them.
    assert _FakeProcessingService.max_seen == 50


def test_content_update_defer_until_uses_next_debounce_boundary(monkeypatch):
    monkeypatch.setattr(
        handlers.datastore_settings, "document_processing_debounce_seconds", 300
    )

    defer_until = handlers._content_update_defer_until(
        datetime(2026, 4, 9, 14, 2, 11, tzinfo=timezone.utc)
    )

    assert defer_until == datetime(2026, 4, 9, 14, 5, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_enqueue_file_processing_defers_content_updates(monkeypatch):
    enqueue_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        handlers.datastore_settings, "document_processing_debounce_seconds", 300
    )
    monkeypatch.setattr(
        handlers,
        "get_datastore_reindex_queue",
        lambda: SimpleNamespace(enqueue=enqueue_mock),
    )

    event = DatastoreFileUpdatedEvent(
        file_id=uuid4(),
        pod_id=uuid4(),
        metadata={"source": "frontend"},
        occurred_at=datetime(2026, 4, 9, 14, 2, 11, tzinfo=timezone.utc),
    )

    await handlers._enqueue_file_processing(
        event, SimpleNamespace(info=lambda *args, **kwargs: None)
    )

    assert enqueue_mock.await_args.kwargs["defer_until"] == datetime(
        2026, 4, 9, 14, 5, 0, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_file_event_wrapper_ignores_other_events_and_validates_inside_inbox(
    monkeypatch,
):
    logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    enqueue = AsyncMock()
    monkeypatch.setattr(handlers, "_enqueue_file_processing", enqueue)

    await handlers.on_datastore_file_event(
        {"event_type": "datastore.record.created"},
        logger,
        inbox=PassthroughEventInbox(),
    )
    enqueue.assert_not_awaited()

    for event in (
        DatastoreFileCreatedEvent(
            file_id=uuid4(),
            pod_id=uuid4(),
            metadata={"kind": "created"},
        ),
        DatastoreFileUpdatedEvent(
            file_id=uuid4(),
            pod_id=uuid4(),
            metadata={"kind": "updated"},
        ),
    ):
        await handlers.on_datastore_file_event(
            event.model_dump(mode="json"),
            logger,
            inbox=PassthroughEventInbox(),
        )

    assert enqueue.await_count == 2
    assert isinstance(enqueue.await_args_list[0].args[0], DatastoreFileCreatedEvent)
    assert isinstance(enqueue.await_args_list[1].args[0], DatastoreFileUpdatedEvent)


@pytest.mark.asyncio
async def test_enqueue_file_processing_covers_disabled_and_duplicate_paths(monkeypatch):
    event = DatastoreFileCreatedEvent(file_id=uuid4(), pod_id=uuid4())
    logger = SimpleNamespace(info=Mock())
    queue = SimpleNamespace(enqueue=AsyncMock(return_value=False))
    monkeypatch.setattr(handlers, "get_datastore_reindex_queue", lambda: queue)

    monkeypatch.setattr(
        handlers.datastore_settings, "e2e_disable_worker_file_autoindex", True
    )
    await handlers._enqueue_file_processing(event, logger)
    queue.enqueue.assert_not_awaited()

    monkeypatch.setattr(
        handlers.datastore_settings, "e2e_disable_worker_file_autoindex", False
    )
    await handlers._enqueue_file_processing(event, logger)
    queue.enqueue.assert_awaited_once()
    logger.info.assert_not_called()
