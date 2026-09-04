from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.domain.ports import (
    DatastoreFileRepositoryPort,
    DatastoreReindexQueuePort,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class DatastoreFileDispatchSummary:
    considered_count: int
    enqueued_count: int
    pod_count: int


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

    async def dispatch_pending_files(
        self,
        *,
        per_pod_limit: int | None = None,
        global_limit: int | None = None,
    ) -> DatastoreFileDispatchSummary:
        """Meter PENDING work into the queue, fairly across pods.

        This is the counterpart to the per-pod admission gate in
        ``RedisDatastoreReindexQueue.enqueue``: uploads beyond a pod's in-flight
        allowance are deliberately left PENDING rather than queued, so the
        Postgres row is the durable backlog and Redis only ever holds a bounded
        working set. This runs often and drains that backlog round-robin, which
        is what guarantees every file is eventually processed even while one
        tenant is bulk-uploading.

        ``bypass_admission`` is passed on purpose: the fairness decision was
        already made by the ranked query, and re-applying the gate here would
        refuse exactly the files this pass just chose.
        """
        if per_pod_limit is None:
            per_pod_limit = datastore_settings.datastore_per_pod_max_inflight
        if global_limit is None:
            global_limit = datastore_settings.datastore_dispatch_global_batch
        # A disabled per-pod gate means "no fairness accounting" — dispatch is
        # then just a bounded FIFO drain.
        effective_per_pod = per_pod_limit if per_pod_limit > 0 else global_limit

        candidates = await self.file_repository.list_pending_dispatch_candidates(
            per_pod_limit=effective_per_pod,
            global_limit=global_limit,
        )
        defer_until = self._backoff_while_the_extractor_is_down()
        batch_size = max(1, datastore_settings.recovery_enqueue_batch_size)
        enqueued_count = 0
        # The candidates are already read; everything below is Redis. Holding
        # the connection through one enqueue per file would mean a whole
        # backlog drain runs with a pooled connection checked out and the
        # database asked nothing.
        async with connection_released(getattr(self.uow, "session", None)):
            for index, file_entity in enumerate(candidates):
                queued = await self.reindex_queue.enqueue(
                    file_id=file_entity.id,
                    pod_id=file_entity.pod_id,
                    metadata=file_entity.metadata or {},
                    defer_until=defer_until,
                    bypass_admission=True,
                )
                if queued:
                    enqueued_count += 1
                # Yield between batches so a large backlog is spread out
                # instead of dispatched as one burst that spikes DB + worker
                # pickup demand.
                if (index + 1) % batch_size == 0:
                    await asyncio.sleep(0)

        return DatastoreFileDispatchSummary(
            considered_count=len(candidates),
            enqueued_count=enqueued_count,
            pod_count=len({file_entity.pod_id for file_entity in candidates}),
        )

    def _backoff_while_the_extractor_is_down(self) -> datetime | None:
        """When to run this batch, given what we know about the extractor.

        ``None`` — meaning now — unless the extractor is known-down, in which
        case the batch is deferred until its circuit would next let a trial
        through.

        This is the *rate* limit that `PS-DATA-041` needs in order to be
        affordable. That promise is deliberate and works: a document whose
        converter is unreachable is returned to PENDING with its attempt
        refunded, so an outage never exhausts a file's retry budget. What it
        does not do is bound how often the hopeless claim is retried -- and this
        dispatcher re-picks every PENDING file on every pass. Four unreadable
        files were enough to re-drive continuously and stall the worker's event
        loop, in a deployment where the visible symptom was agent replies going
        slow in unrelated pods. One tenant's unreadable PDFs became everybody's
        latency, which is the shape `PS-DATA-042`'s backpressure exists to stop
        for uploads.

        Deferring rather than skipping matters: the files stay queued and still
        drain by themselves once the extractor returns, so nothing waits on the
        next cron tick. The attempt counter is untouched either way, so
        `PS-DATA-041` is unaffected -- what changes is how often a hopeless
        claim is retried, not how many failures it is charged with.
        """
        from app.modules.datastore.infrastructure.kreuzberg_circuit import (
            get_kreuzberg_circuit,
        )

        cooldown = get_kreuzberg_circuit().seconds_until_trial()
        if cooldown <= 0:
            return None
        logger.info(
            "datastore.file_recovery_service.dispatch_deferred_extractor_down.degraded",
            cooldown_seconds=round(cooldown, 1),
        )
        return datetime.now(timezone.utc) + timedelta(seconds=cooldown)

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
        # Same shape as the dispatch pass above: the rows are read, the rest
        # is Redis.
        async with connection_released(getattr(self.uow, "session", None)):
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
