"""Retrying a document whose processing failed.

The library shows a failed file with a Retry; this is what that button does.
Only a failed file is re-opened, with a fresh attempt budget, and the event it
sends is the one that enqueues a PENDING row for the worker.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.domain.events import DatastoreFileUpdatedEvent
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileStatus,
)
from app.modules.datastore.services.files.transaction_writer import (
    FileTransactionWriter,
)

pytestmark = pytest.mark.unit


def _file(status: FileStatus) -> DatastoreFileEntity:
    return DatastoreFileEntity(
        pod_id=uuid4(),
        path="/papers/paper.pdf",
        kind="FILE",
        name="paper.pdf",
        mime_type="application/pdf",
        status=status,
        processing_attempts=3,
        last_processing_error="ValueError: document processing failed",
    )


@pytest.mark.parametrize("status", [FileStatus.FAILED, FileStatus.FAILED_PERMANENT])
def test_a_failed_file_is_reopened_with_a_fresh_budget(status):
    entity = _file(status)
    actor = uuid4()

    assert entity.mark_retry_requested(actor) is True

    assert entity.status == FileStatus.PENDING
    assert entity.processing_attempts == 0
    assert entity.last_processing_error is None
    events = entity.collect_events()
    assert any(isinstance(event, DatastoreFileUpdatedEvent) for event in events)


@pytest.mark.parametrize(
    "status",
    [FileStatus.PENDING, FileStatus.PROCESSING, FileStatus.COMPLETED],
)
def test_a_file_that_did_not_fail_is_left_alone(status):
    entity = _file(status)

    assert entity.mark_retry_requested(uuid4()) is False
    assert entity.status == status
    assert entity.processing_attempts == 3


def _writer(entity: DatastoreFileEntity) -> FileTransactionWriter:
    writer = FileTransactionWriter.__new__(FileTransactionWriter)
    writer.reader = AsyncMock()
    writer.reader.get_file_by_path.return_value = entity
    writer.authorizer = AsyncMock()
    writer.file_repository = AsyncMock()
    writer.file_repository.update.side_effect = lambda saved: saved
    return writer


@pytest.mark.asyncio
async def test_retry_requires_write_permission_and_persists_the_reopen():
    entity = _file(FileStatus.FAILED_PERMANENT)
    writer = _writer(entity)
    user = uuid4()

    result = await writer.retry_processing(entity.pod_id, entity.path, user)

    writer.authorizer.require_file_write_permission.assert_awaited_once()
    writer.file_repository.update.assert_awaited_once_with(entity)
    assert result.status == FileStatus.PENDING


@pytest.mark.asyncio
async def test_retrying_a_healthy_file_writes_nothing():
    entity = _file(FileStatus.COMPLETED)
    writer = _writer(entity)

    result = await writer.retry_processing(entity.pod_id, entity.path, uuid4())

    writer.file_repository.update.assert_not_awaited()
    assert result is entity
