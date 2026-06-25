from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.modules.datastore.config import datastore_settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.stream_subscriber import redis_stream_sub
from app.modules.datastore.api.dependencies import (
    build_pod_member_sync_service,
)
from app.modules.datastore.domain.events import (
    DATASTORE_EVENTS_STREAM,
    DatastoreFileCreatedEvent,
    DatastoreFileUpdatedEvent,
)
from app.modules.datastore.infrastructure.repositories import (
    DatastoreFileRepository,
)
from app.modules.datastore.infrastructure.reindex_queue import (
    get_datastore_reindex_queue,
)
from app.modules.datastore.services.file_processing_service import (
    DatastoreFileProcessingService,
)
from app.modules.datastore.services.file_recovery_service import (
    DatastoreFileRecoveryService,
)
from app.modules.pod.domain.events import (
    PodEvents,
    PodMemberAddedEvent,
    PodMemberRemovedEvent,
)
from app.core.infrastructure.jobs.streaq_runtime import AppWorkerContext, streaq_cron, streaq_task, streaq_worker
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()
_document_processing_semaphore: asyncio.Semaphore | None = None
_document_processing_semaphore_limit: int | None = None


def _get_document_processing_semaphore() -> asyncio.Semaphore:
    global _document_processing_semaphore, _document_processing_semaphore_limit

    limit = max(1, datastore_settings.document_processing_max_concurrency)
    if (
        _document_processing_semaphore is None
        or _document_processing_semaphore_limit != limit
    ):
        _document_processing_semaphore = asyncio.Semaphore(limit)
        _document_processing_semaphore_limit = limit

    return _document_processing_semaphore


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def _content_update_defer_until(occurred_at: datetime) -> datetime | None:
    debounce_seconds = max(0, datastore_settings.document_processing_debounce_seconds)
    if debounce_seconds == 0:
        return None

    occurred_at_utc = occurred_at.astimezone(timezone.utc)
    scheduled_epoch = math.ceil(occurred_at_utc.timestamp() / debounce_seconds) * debounce_seconds
    scheduled_at = datetime.fromtimestamp(scheduled_epoch, tz=timezone.utc)
    if scheduled_at <= occurred_at_utc:
        scheduled_at = occurred_at_utc + timedelta(seconds=debounce_seconds)
    return scheduled_at


async def _enqueue_file_processing(
    event: DatastoreFileCreatedEvent | DatastoreFileUpdatedEvent,
    fs_logger: Logger,
) -> None:
    defer_until = None
    if event.event_type == DatastoreFileUpdatedEvent.get_event_type():
        defer_until = _content_update_defer_until(event.occurred_at)

    queued = await get_datastore_reindex_queue().enqueue(
        file_id=event.file_id,
        pod_id=event.pod_id,
        metadata=event.metadata,
        defer_until=defer_until,
    )
    if queued:
        fs_logger.info("Enqueued process_datastore_file_task for %s", event.file_id)
    else:
        fs_logger.info(
            "Skipped enqueue for %s because it was duplicate or not eligible",
            event.file_id,
        )


@router.subscriber(stream=redis_stream_sub(PodEvents.STREAM))
async def handle_pod_member_sync(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
):
    event_type = event.get("event_type")

    if event_type == PodMemberAddedEvent.get_event_type():
        parsed = PodMemberAddedEvent.model_validate(event)
        async with uow_factory() as uow:
            await build_pod_member_sync_service(uow).sync_member_added(parsed)
        fs_logger.info("Synced pod.member.added for pod %s", parsed.pod_id)
        return

    if event_type == PodMemberRemovedEvent.get_event_type():
        parsed = PodMemberRemovedEvent.model_validate(event)
        async with uow_factory() as uow:
            await build_pod_member_sync_service(uow).sync_member_removed(parsed)
        fs_logger.info("Synced pod.member.removed for pod %s", parsed.pod_id)


@router.subscriber(stream=redis_stream_sub(DATASTORE_EVENTS_STREAM))
async def on_datastore_file_event(event: dict, fs_logger: Logger):
    # The unified datastore stream also carries table/record events; ignore
    # everything that is not a file event.
    event_type = event.get("event_type")
    try:
        if event_type == DatastoreFileCreatedEvent.get_event_type():
            parsed = DatastoreFileCreatedEvent.model_validate(event)
            await _enqueue_file_processing(parsed, fs_logger)
            return

        if event_type == DatastoreFileUpdatedEvent.get_event_type():
            parsed = DatastoreFileUpdatedEvent.model_validate(event)
            await _enqueue_file_processing(parsed, fs_logger)
    except Exception as exc:
        fs_logger.error("Failed to handle datastore file event %s: %s", event_type, exc)


@streaq_task(name="process_datastore_file_task")
async def process_datastore_file_task(
    _task_context=None,
    *,
    file_id: str,
    pod_id: str,
    metadata: dict | None = None,
):
    try:
        worker_ctx: AppWorkerContext | None = streaq_worker.context
    except Exception:
        worker_ctx = None
    file_uuid = UUID(file_id)
    pod_uuid = UUID(pod_id)

    logger.info("STARTING process_datastore_file_task for %s", file_uuid)

    try:
        async with _get_document_processing_semaphore():
            # The service uses a uow_factory to open SHORT UoWs around each DB
            # op, releasing the pooled connection during the slow storage
            # downloads, document extraction, and uploads inside
            # process_file_async. The semaphore still caps extraction
            # CPU/memory and concurrent datastore-pool use during indexing.
            uow_factory = (
                worker_ctx.uow_factory
                if worker_ctx is not None
                else SessionUnitOfWorkFactory(async_session_maker)
            )
            service = DatastoreFileProcessingService(
                pod_uuid, uow_factory=uow_factory
            )
            await service.process_file_async(file_uuid, metadata or {})
        logger.info("FINISHED process_datastore_file_task for %s", file_uuid)
    except Exception as exc:
        logger.error("process_datastore_file_task failed for %s: %s", file_uuid, exc)
        raise


@streaq_cron("*/15 * * * *", name="recover_stuck_processing_files")
async def recover_stuck_processing_files(
) -> None:
    """
    Find files stuck in PENDING or PROCESSING and make sure they get queued.

    This is necessary because files can get stranded in PENDING before a worker picks them up,
    and the queue does not auto-recover jobs from crashed workers that leave rows in PROCESSING.
    The cron re-enqueues stale PENDING files and resets stale PROCESSING files back to PENDING
    before re-enqueueing them.

    PENDING files are retried after 15 minutes. PROCESSING files are only reclaimed after
    35 minutes, which preserves a 5-minute buffer beyond the worker's in-progress timeout.
    """
    worker_ctx: AppWorkerContext = streaq_worker.context
    try:
        async with worker_ctx.uow() as uow:
            recovery_service = DatastoreFileRecoveryService(
                file_repository=DatastoreFileRepository(uow),
                reindex_queue=get_datastore_reindex_queue(),
                uow=uow,
            )
            summary = await recovery_service.recover_stale_files(now=datetime.now(timezone.utc))

        logger.info(
            "Running datastore file recovery (pending_cutoff=%s, processing_cutoff=%s)",
            summary.pending_cutoff,
            summary.processing_cutoff,
        )

        if summary.examined_count == 0:
            logger.info("No stale datastore files found.")
            return

        logger.info(
            "Datastore file recovery examined %d stale files, reset %d PROCESSING files to PENDING, enqueued %d.",
            summary.examined_count,
            summary.reset_count,
            summary.enqueued_count,
        )
    except Exception as exc:
        logger.error("Stuck file recovery cron failed: %s", exc)
