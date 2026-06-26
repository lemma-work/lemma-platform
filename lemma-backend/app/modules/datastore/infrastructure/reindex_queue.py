"""Redis-backed queue for datastore file reindex jobs with streaq task-id deduplication."""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import select
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    get_streaq_job_queue,
)
from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.domain.ports import DatastoreReindexQueuePort
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.core.log.log import get_logger

logger = get_logger(__name__)


class RedisDatastoreReindexQueue(DatastoreReindexQueuePort):
    def __init__(
        self,
        job_queue: SharedStreaqJobQueue,
    ):
        self._job_queue = job_queue

    async def close(self) -> None:
        return None

    async def _is_file_pending(
        self,
        *,
        file_id: UUID,
        pod_id: UUID,
    ) -> bool:
        async with async_session_maker() as session:
            result = await session.execute(
                select(DatastoreFile.status).where(
                    DatastoreFile.id == file_id,
                    DatastoreFile.pod_id == pod_id,
                    DatastoreFile.kind == "FILE",
                    DatastoreFile.search_enabled == True,  # noqa: E712
                )
            )
            status = result.scalar_one_or_none()

        if status is None:
            logger.info("Skipped enqueue for %s: file is missing or not searchable", file_id)
            return False

        if status != FileStatus.PENDING.value:
            logger.info(
                "Skipped enqueue for %s: status %s is not eligible for processing",
                file_id,
                status,
            )
            return False

        return True

    def _job_id(self, *, file_id: UUID, defer_until: datetime | None) -> str:
        if defer_until is None:
            return f"datastore_file:{file_id}"
        return f"datastore_file:{file_id}:{int(defer_until.timestamp())}"

    async def enqueue(
        self,
        *,
        file_id: UUID,
        pod_id: UUID,
        metadata: dict | None,
        defer_until: datetime | None = None,
    ) -> bool:
        is_pending = await self._is_file_pending(file_id=file_id, pod_id=pod_id)
        if not is_pending:
            return False

        job_id = self._job_id(file_id=file_id, defer_until=defer_until)
        result = await self._job_queue.enqueue(
            "process_datastore_file_task",
            _job_id=job_id,
            _defer_until=defer_until,
            file_id=str(file_id),
            pod_id=str(pod_id),
            metadata=metadata or {},
        )
        if result is None:
            logger.info(
                "Skipped duplicate reindex enqueue for %s with job %s",
                file_id,
                job_id,
            )
            return False
        return True


_reindex_queue: RedisDatastoreReindexQueue | None = None


def get_datastore_reindex_queue() -> RedisDatastoreReindexQueue:
    global _reindex_queue
    if _reindex_queue is None:
        _reindex_queue = RedisDatastoreReindexQueue(
            job_queue=get_streaq_job_queue(),
        )
    return _reindex_queue


async def close_datastore_reindex_queue() -> None:
    global _reindex_queue
    if _reindex_queue is not None:
        await _reindex_queue.close()
        _reindex_queue = None


async def enqueue_datastore_path_cleanup(
    *,
    pod_id: UUID,
    is_folder: bool,
    folder_prefix: str | None,
    files: list[dict[str, str]],
) -> bool:
    """Offload storage + search-index cleanup for already-deleted file/folder
    rows to the worker (cleanup_deleted_datastore_paths task). Returns True if
    the task was enqueued; the caller should clean up in-process on a False/raise
    so deleted rows never leave orphaned blobs."""
    result = await get_streaq_job_queue().enqueue(
        "cleanup_deleted_datastore_paths",
        pod_id=str(pod_id),
        is_folder=is_folder,
        folder_prefix=folder_prefix,
        files=files,
    )
    return result is not None
