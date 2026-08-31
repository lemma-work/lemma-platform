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


# --- Fair dispatch --------------------------------------------------------
#
# Ingestion is shared capacity. Plain FIFO lets one pod that uploads a thousand
# papers hold the queue until it drains, so everyone else waits. These cover the
# two halves of the fix: the ranked query spreads work across pods, and the
# dispatcher must not be re-gated by the admission check that deferred the work
# to it in the first place.


def _dispatch_repo(candidates):
    repo = AsyncMock()
    repo.list_pending_dispatch_candidates.return_value = list(candidates)
    return repo


def _file(pod_id):
    return SimpleNamespace(
        id=uuid4(), pod_id=pod_id, metadata={}, status=FileStatus.PENDING
    )


@pytest.mark.asyncio
async def test_dispatch_enqueues_candidates_and_reports_pod_spread():
    pod_a, pod_b = uuid4(), uuid4()
    candidates = [_file(pod_a), _file(pod_b), _file(pod_a)]
    repo = _dispatch_repo(candidates)
    queue = AsyncMock()
    queue.enqueue.return_value = True

    service = DatastoreFileRecoveryService(
        file_repository=repo, reindex_queue=queue, uow=AsyncMock()
    )
    summary = await service.dispatch_pending_files(per_pod_limit=2, global_limit=50)

    assert summary.considered_count == 3
    assert summary.enqueued_count == 3
    assert summary.pod_count == 2
    repo.list_pending_dispatch_candidates.assert_awaited_once_with(
        per_pod_limit=2, global_limit=50
    )


@pytest.mark.asyncio
async def test_dispatch_bypasses_the_admission_gate():
    """Otherwise the gate would refuse exactly the files this pass just chose.

    The per-pod gate is what deferred these rows to the dispatcher; re-applying
    it here would deadlock the backlog — nothing would ever drain.
    """
    repo = _dispatch_repo([_file(uuid4())])
    queue = AsyncMock()
    queue.enqueue.return_value = True

    service = DatastoreFileRecoveryService(
        file_repository=repo, reindex_queue=queue, uow=AsyncMock()
    )
    await service.dispatch_pending_files(per_pod_limit=1, global_limit=10)

    assert queue.enqueue.await_args.kwargs["bypass_admission"] is True


@pytest.mark.asyncio
async def test_dispatch_with_fairness_disabled_falls_back_to_bounded_fifo():
    """per_pod_limit=0 means 'no fairness accounting', not 'dispatch nothing'."""
    repo = _dispatch_repo([])
    queue = AsyncMock()

    service = DatastoreFileRecoveryService(
        file_repository=repo, reindex_queue=queue, uow=AsyncMock()
    )
    await service.dispatch_pending_files(per_pod_limit=0, global_limit=25)

    # Falls back to the global bound rather than passing 0 through, which would
    # select no rows and stall ingestion permanently.
    repo.list_pending_dispatch_candidates.assert_awaited_once_with(
        per_pod_limit=25, global_limit=25
    )


# --- Backing off a dead extractor -----------------------------------------
#
# `PS-DATA-041` refunds the attempt when the converter is unreachable, so an
# outage never exhausts a file's retry budget. That is deliberate and it works.
# What it does not do on its own is bound how *often* the hopeless claim is
# retried: this dispatcher re-picks every PENDING file on every pass, so a file
# no converter can reach is re-driven as fast as the queue allows. Four such
# files were enough to stall the worker's event loop, and the symptom showed up
# as slow agent replies in unrelated pods.


@pytest.mark.asyncio
async def test_dispatch_runs_now_while_the_extractor_is_healthy():
    """The common case pays nothing: no circuit open, no deferral."""
    from app.modules.datastore.infrastructure.kreuzberg_circuit import (
        reset_kreuzberg_circuit,
    )

    reset_kreuzberg_circuit()
    repo = _dispatch_repo([_file(uuid4())])
    queue = AsyncMock()
    queue.enqueue.return_value = True

    service = DatastoreFileRecoveryService(
        file_repository=repo, reindex_queue=queue, uow=AsyncMock()
    )
    await service.dispatch_pending_files(per_pod_limit=1, global_limit=10)

    assert queue.enqueue.await_args.kwargs["defer_until"] is None


@pytest.mark.asyncio
async def test_dispatch_defers_the_batch_while_the_extractor_is_down():
    """A known-down extractor slows the re-drive instead of stopping it.

    Deferred rather than skipped, so the backlog still drains by itself when
    the extractor returns rather than waiting for the next cron tick. The
    attempt counter is untouched either way — this bounds the rate, not the
    budget, so `PS-DATA-041` is unaffected.
    """
    from app.modules.datastore.infrastructure.kreuzberg_circuit import (
        get_kreuzberg_circuit,
        reset_kreuzberg_circuit,
    )

    reset_kreuzberg_circuit()
    circuit = get_kreuzberg_circuit()
    for _ in range(50):  # comfortably past any configured threshold
        circuit.record_failure()
    assert circuit.is_open, "the breaker did not open; this test proves nothing"

    repo = _dispatch_repo([_file(uuid4()), _file(uuid4())])
    queue = AsyncMock()
    queue.enqueue.return_value = True

    service = DatastoreFileRecoveryService(
        file_repository=repo, reindex_queue=queue, uow=AsyncMock()
    )
    try:
        await service.dispatch_pending_files(per_pod_limit=5, global_limit=10)

        deferrals = [
            call.kwargs["defer_until"] for call in queue.enqueue.await_args_list
        ]
        assert all(when is not None for when in deferrals), (
            f"the extractor is down and the batch was still dispatched now: {deferrals}"
        )
        assert len(set(deferrals)) == 1, (
            "one pass should share one deadline, so the queue de-duplicates it"
        )
    finally:
        reset_kreuzberg_circuit()
