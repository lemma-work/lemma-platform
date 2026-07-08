from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.services.file_recovery_service import (
    DatastoreFileRecoveryService,
)


def _make_repo(*, stale=None, exhausted=None, reset_count=1):
    """AsyncMock repo with both recovery queries stubbed.

    list_exhausted_recovery_candidates must be explicitly stubbed to [] or the
    default AsyncMock return (a MagicMock) is not iterable.
    """
    repo = AsyncMock()
    repo.list_stale_recovery_candidates.return_value = list(stale or [])
    repo.list_exhausted_recovery_candidates.return_value = list(exhausted or [])
    repo.bulk_update_status.return_value = reset_count
    repo.bulk_mark_failed_permanent.return_value = len(exhausted or [])
    return repo


@pytest.mark.asyncio
async def test_recover_stale_files_resets_processing_and_reenqueues_all():
    pending_file = SimpleNamespace(
        id=uuid4(),
        pod_id=uuid4(),
        metadata={"source": "pending"},
        status=FileStatus.PENDING,
    )
    processing_file = SimpleNamespace(
        id=uuid4(),
        pod_id=uuid4(),
        metadata={"source": "processing"},
        status=FileStatus.PROCESSING,
    )
    file_repository = _make_repo(stale=[pending_file, processing_file])

    reindex_queue = AsyncMock()
    reindex_queue.enqueue = AsyncMock(side_effect=[True, False])
    uow = AsyncMock()

    service = DatastoreFileRecoveryService(
        file_repository=file_repository,
        reindex_queue=reindex_queue,
        uow=uow,
    )

    summary = await service.recover_stale_files(
        now=datetime(2026, 4, 9, 14, 0, tzinfo=timezone.utc)
    )

    assert summary.examined_count == 2
    assert summary.reset_count == 1
    assert summary.enqueued_count == 1
    assert summary.terminal_count == 0
    file_repository.bulk_update_status.assert_awaited_once_with(
        file_ids=[processing_file.id],
        status=FileStatus.PENDING,
    )
    file_repository.bulk_mark_failed_permanent.assert_not_awaited()
    assert reindex_queue.enqueue.await_count == 2
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_stale_files_resets_and_reenqueues_failed_files():
    failed_file = SimpleNamespace(
        id=uuid4(),
        pod_id=uuid4(),
        metadata={"source": "failed"},
        status=FileStatus.FAILED,
    )
    file_repository = _make_repo(stale=[failed_file])
    reindex_queue = AsyncMock()
    reindex_queue.enqueue = AsyncMock(return_value=True)
    uow = AsyncMock()

    service = DatastoreFileRecoveryService(
        file_repository=file_repository,
        reindex_queue=reindex_queue,
        uow=uow,
    )

    summary = await service.recover_stale_files(
        now=datetime(2026, 4, 9, 14, 0, tzinfo=timezone.utc)
    )

    # FAILED files must be reset to PENDING and re-enqueued, and a failed_cutoff
    # must be passed to the candidate query.
    file_repository.bulk_update_status.assert_awaited_once_with(
        file_ids=[failed_file.id],
        status=FileStatus.PENDING,
    )
    call = file_repository.list_stale_recovery_candidates.await_args
    assert call.kwargs["failed_cutoff"] is not None
    assert summary.reset_count == 1
    assert summary.enqueued_count == 1
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_stale_files_skips_commit_when_nothing_processing():
    pending_file = SimpleNamespace(
        id=uuid4(),
        pod_id=uuid4(),
        metadata={},
        status=FileStatus.PENDING,
    )
    file_repository = _make_repo(stale=[pending_file])
    reindex_queue = AsyncMock()
    reindex_queue.enqueue = AsyncMock(return_value=True)
    uow = AsyncMock()

    service = DatastoreFileRecoveryService(
        file_repository=file_repository,
        reindex_queue=reindex_queue,
        uow=uow,
    )

    summary = await service.recover_stale_files(
        now=datetime(2026, 4, 9, 14, 0, tzinfo=timezone.utc)
    )

    assert summary.reset_count == 0
    assert summary.terminal_count == 0
    file_repository.bulk_update_status.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_recover_stale_files_terminally_fails_exhausted_files():
    """Files past the attempt cap are marked FAILED_PERMANENT and NOT re-driven.

    This is the fix for the OOM poison-loop: an OOM-killed file stranded in
    PROCESSING (its mark_failed never ran) must eventually stop being re-driven.
    """
    exhausted_processing = SimpleNamespace(
        id=uuid4(), pod_id=uuid4(), metadata={}, status=FileStatus.PROCESSING
    )
    exhausted_failed = SimpleNamespace(
        id=uuid4(), pod_id=uuid4(), metadata={}, status=FileStatus.FAILED
    )
    # No under-cap stale files this round — only exhausted ones.
    file_repository = _make_repo(
        stale=[], exhausted=[exhausted_processing, exhausted_failed], reset_count=0
    )
    reindex_queue = AsyncMock()
    reindex_queue.enqueue = AsyncMock(return_value=True)
    uow = AsyncMock()

    service = DatastoreFileRecoveryService(
        file_repository=file_repository,
        reindex_queue=reindex_queue,
        uow=uow,
    )

    summary = await service.recover_stale_files(
        now=datetime(2026, 4, 9, 14, 0, tzinfo=timezone.utc)
    )

    assert summary.terminal_count == 2
    file_repository.bulk_mark_failed_permanent.assert_awaited_once()
    kwargs = file_repository.bulk_mark_failed_permanent.await_args.kwargs
    assert set(kwargs["file_ids"]) == {
        exhausted_processing.id,
        exhausted_failed.id,
    }
    # Exhausted files are terminally failed, never re-enqueued.
    reindex_queue.enqueue.assert_not_awaited()
    # The terminal transition is committed.
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_stale_files_passes_max_attempts_to_both_queries():
    file_repository = _make_repo(stale=[], exhausted=[])
    reindex_queue = AsyncMock()
    uow = AsyncMock()

    service = DatastoreFileRecoveryService(
        file_repository=file_repository,
        reindex_queue=reindex_queue,
        uow=uow,
    )

    await service.recover_stale_files(
        now=datetime(2026, 4, 9, 14, 0, tzinfo=timezone.utc),
        max_attempts=2,
    )

    stale_call = file_repository.list_stale_recovery_candidates.await_args
    exhausted_call = file_repository.list_exhausted_recovery_candidates.await_args
    assert stale_call.kwargs["max_attempts"] == 2
    assert exhausted_call.kwargs["max_attempts"] == 2
