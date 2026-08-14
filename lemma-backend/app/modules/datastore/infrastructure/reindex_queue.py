"""Redis-backed queue for datastore file reindex jobs with streaq task-id deduplication."""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import func, select
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    get_streaq_job_queue,
)
from app.modules.datastore.config import datastore_settings
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

    async def _read_admission_state(
        self,
        *,
        file_id: UUID,
        pod_id: UUID,
        need_pod_count: bool,
    ) -> tuple[bool, int]:
        """Whether the file is still PENDING, and how much else the pod has active.

        Both answers come from one session on purpose. They were two separate
        round-trips, which is wasteful on a path that runs once per uploaded
        file, and it also made the enqueue decision span two points in time.

        ``file_id`` is excluded from the active count: its own row is already
        PENDING by the time this runs, so counting it would mean a pod at limit 4
        admits only 3 — and at a limit of 1, nothing at all.

        ``need_pod_count`` skips the count when the caller is going to ignore it.
        The dispatch cron bypasses admission for every file it offers — the
        fairness decision was already made by the ranked query — so counting
        there was a per-file aggregate computed only to be discarded, once a
        minute, for as long as the backlog lasted.
        """
        active_statuses = (FileStatus.PENDING.value, FileStatus.PROCESSING.value)
        async with async_session_maker() as session:
            status = await session.scalar(
                select(DatastoreFile.status).where(
                    DatastoreFile.id == file_id,
                    DatastoreFile.pod_id == pod_id,
                    DatastoreFile.kind == "FILE",
                    DatastoreFile.search_enabled == True,  # noqa: E712
                )
            )
            if status != FileStatus.PENDING.value:
                # Not eligible either way; skip the count entirely.
                return False, 0
            if not need_pod_count:
                return True, 0
            active = int(
                await session.scalar(
                    select(func.count())
                    .select_from(DatastoreFile)
                    .where(
                        DatastoreFile.pod_id == pod_id,
                        DatastoreFile.id != file_id,
                        DatastoreFile.kind == "FILE",
                        DatastoreFile.search_enabled == True,  # noqa: E712
                        DatastoreFile.status.in_(active_statuses),
                    )
                )
                or 0
            )
        return True, active

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
        bypass_admission: bool = False,
    ) -> bool:
        """Queue a file for processing, subject to the per-pod admission gate.

        Returning False when the pod is saturated is not a failure and drops
        nothing: the row stays PENDING, which IS the durable backlog, and
        ``dispatch_pending_datastore_files`` will pick it up fairly. The
        dispatcher itself passes ``bypass_admission`` — it has already done the
        fairness accounting and must not be re-gated by it.
        """
        limit = datastore_settings.datastore_per_pod_max_inflight
        gate_applies = not bypass_admission and limit > 0
        is_pending, pod_active = await self._read_admission_state(
            file_id=file_id,
            pod_id=pod_id,
            need_pod_count=gate_applies,
        )
        if not is_pending:
            return False

        if gate_applies and pod_active >= limit:
            logger.debug(
                "datastore.reindex_queue.pod_admission_deferred_to_dispatcher.observed",
                pod_id=str(pod_id),
            )
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
