from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.domain.ports import (
    DatastoreFileRepositoryPort,
    DatastoreReindexQueuePort,
)


@dataclass(frozen=True)
class DatastoreFileRecoverySummary:
    examined_count: int
    reset_count: int
    enqueued_count: int
    terminal_count: int
    pending_cutoff: datetime
    processing_cutoff: datetime


class DatastoreFileRecoveryService:
    def __init__(
        self,
        *,
        file_repository: DatastoreFileRepositoryPort,
        reindex_queue: DatastoreReindexQueuePort,
        uow: SqlAlchemyUnitOfWork,
    ) -> None:
        self.file_repository = file_repository
        self.reindex_queue = reindex_queue
        self.uow = uow

    async def recover_stale_files(
        self,
        *,
        now: datetime | None = None,
        max_attempts: int | None = None,
    ) -> DatastoreFileRecoverySummary:
        current_time = now or datetime.now(timezone.utc)
        if max_attempts is None:
            max_attempts = datastore_settings.datastore_recovery_max_attempts
        pending_cutoff = current_time - timedelta(minutes=15)
        processing_cutoff = current_time - timedelta(minutes=35)
        failed_cutoff = current_time - timedelta(minutes=30)

        # First, terminally fail files that have exhausted their retry budget so
        # the cron stops resurrecting them. Without this a file stranded in
        # PROCESSING by an OOM-killed worker (its mark_failed never ran) would be
        # re-driven forever — the exact poison-queue loop that OOM'd the worker.
        exhausted = await self.file_repository.list_exhausted_recovery_candidates(
            processing_cutoff=processing_cutoff,
            failed_cutoff=failed_cutoff,
            max_attempts=max_attempts,
        )
        terminal_count = 0
        if exhausted:
            terminal_count = await self.file_repository.bulk_mark_failed_permanent(
                file_ids=[file_entity.id for file_entity in exhausted],
                error=f"max processing attempts exceeded ({max_attempts})",
            )
            await self.uow.commit()

        stale_files = await self.file_repository.list_stale_recovery_candidates(
            pending_cutoff=pending_cutoff,
            processing_cutoff=processing_cutoff,
            failed_cutoff=failed_cutoff,
            max_attempts=max_attempts,
        )
        # Stuck PROCESSING and retry-eligible FAILED files must be reset to
        # PENDING before re-enqueue so the processing task's claim guard accepts
        # them.
        reset_ids = [
            file_entity.id
            for file_entity in stale_files
            if file_entity.status in (FileStatus.PROCESSING, FileStatus.FAILED)
        ]
        reset_count = 0
        if reset_ids:
            reset_count = await self.file_repository.bulk_update_status(
                file_ids=reset_ids,
                status=FileStatus.PENDING,
            )
            await self.uow.commit()

        # Re-drive in bounded batches, yielding between them, so a large backlog
        # is spread out instead of dispatched as one burst that spikes worker
        # pickup + DB connection demand.
        batch_size = max(1, datastore_settings.recovery_enqueue_batch_size)
        enqueued_count = 0
        for index, file_entity in enumerate(stale_files):
            # Indexing-eligibility is NOT re-checked here: ``reindex_queue.enqueue``
            # gates on PENDING + search_enabled, which is the same rule the
            # indexing policy enforces at write time. Non-indexable files are
            # persisted as NOT_REQUIRED and so are never recovery candidates nor
            # re-enqueued. The rule lives in the queue/policy, not duplicated here.
            queued = await self.reindex_queue.enqueue(
                file_id=file_entity.id,
                pod_id=file_entity.pod_id,
                metadata=file_entity.metadata or {},
                defer_until=None,
            )
            if queued:
                enqueued_count += 1
            if (index + 1) % batch_size == 0:
                await asyncio.sleep(0)

        return DatastoreFileRecoverySummary(
            examined_count=len(stale_files),
            reset_count=reset_count,
            enqueued_count=enqueued_count,
            terminal_count=terminal_count,
            pending_cutoff=pending_cutoff,
            processing_cutoff=processing_cutoff,
        )
