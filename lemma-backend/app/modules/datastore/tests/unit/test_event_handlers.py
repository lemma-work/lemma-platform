from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.datastore.events import handlers
from app.modules.datastore.domain.events import (
    DatastoreFileCreatedEvent,
    DatastoreFileUpdatedEvent,
)
from app.modules.test_support.fakes import PassthroughEventInbox


@pytest.mark.asyncio
async def test_process_datastore_file_task_limits_document_processing_concurrency(
    monkeypatch,
):
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
    monkeypatch.setattr(
        handlers.datastore_settings,
        "document_processing_max_concurrency",
        20,
    )
    handlers._document_processing_semaphore = None
    handlers._document_processing_semaphore_limit = None

    pod_id = str(uuid4())
    await asyncio.gather(
        *[
            handlers.process_datastore_file_task(
                None,
                file_id=str(uuid4()),
                pod_id=pod_id,
                metadata={"index": index},
            )
            for index in range(100)
        ]
    )

    assert len(processed) == 100
    assert _FakeProcessingService.max_seen <= 20


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

    monkeypatch.setattr(handlers.settings, "e2e_disable_worker_file_autoindex", True)
    await handlers._enqueue_file_processing(event, logger)
    queue.enqueue.assert_not_awaited()

    monkeypatch.setattr(handlers.settings, "e2e_disable_worker_file_autoindex", False)
    await handlers._enqueue_file_processing(event, logger)
    queue.enqueue.assert_awaited_once()
    logger.info.assert_not_called()
