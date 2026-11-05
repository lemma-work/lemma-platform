"""Datastore file processing and cleanup as work items.

``file.process`` is written in the transaction that makes a file need
processing -- an upload, new bytes, a retry, search turned on -- so there is no
event stream, consumer group, admission queue or dispatcher cron between a
write and its processing, and nothing to lose between them. Per-pod fairness is
the ingestion class's per-namespace cap; a file has at most one run at a time
(concurrency key ``file:<id>``); and the retry budgets are the runtime's:

* attempts (``DOCUMENT_PROCESSING_MAX_ATTEMPTS``) for failures the document
  causes, including an extractor 5xx or timeout on it while the extractor
  serves others;
* time (``DOCUMENT_PROCESSING_UNAVAILABLE_BUDGET_SECONDS``) for the extractor or
  embedding provider being down, which is never charged as an attempt;
* lease losses for the worker dying under it.

Whichever runs out, the dead hook writes ``FAILED_PERMANENT`` with the reason.

``file.cleanup`` is written in the delete transaction and purges the deleted
rows' bytes and index entries afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    was_database_unavailable,
    DeadContext,
    Exponential,
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings

logger = get_logger(__name__)

FILE_PROCESS = "file.process"
FILE_CLEANUP = "file.cleanup"


class FileProcessPayload(BaseModel):
    file_id: UUID
    #: The bytes this run is for. A run whose row now holds other bytes stops:
    #: the write that replaced them enqueued its own.
    sha256: str | None = None


class CleanupFile(BaseModel):
    file_id: UUID
    path: str
    storage_key: str
    #: Every revision of the file, in the object layout.
    object_prefix: str | None = None


class FileCleanupPayload(BaseModel):
    is_folder: bool
    folder_prefix: str | None = None
    files: list[CleanupFile] = Field(default_factory=list)


def processing_retry() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max(1, datastore_settings.document_processing_max_attempts),
        max_lease_losses=3,
        unavailable_budget=timedelta(
            seconds=datastore_settings.document_processing_unavailable_budget_seconds
        ),
        backoff=Exponential(
            base=timedelta(seconds=30), cap=timedelta(hours=1), jitter=0.2
        ),
    )


def _pod_of(ns: str) -> UUID:
    return UUID(ns.removeprefix("pod:"))


class _JobRun:
    """A processing run driven by a work item: fenced completion, safe points."""

    def __init__(self, ctx: JobContext) -> None:
        self._ctx = ctx

    def checkpoint(self):
        return self._ctx.checkpoint()

    def completion(self):
        return self._ctx.complete_work()

    def raise_if_stopping(self) -> None:
        self._ctx.raise_if_stopping()


async def run_file_process(ctx: JobContext, payload: FileProcessPayload) -> None:
    from app.modules.datastore.composition import get_datastore_composition

    service = get_datastore_composition().build_processing_service(
        _pod_of(ctx.item.ns), uow_factory=ctx.checkpoint
    )
    await service.process(payload.file_id, sha256=payload.sha256, run=_JobRun(ctx))


def dead_message(reason: str, last_error: str | None) -> str:
    """What a person reads on a file whose processing gave up, and why."""
    if reason == "attempts_exhausted":
        return f"document_unprocessable: {last_error or 'processing failed repeatedly'}"
    if reason == "unavailable_exhausted":
        hours = datastore_settings.document_processing_unavailable_budget_seconds / 3600
        if was_database_unavailable(last_error):
            return (
                "database_unavailable: Lemma's database was unavailable for "
                f"longer than {hours:g} hours while this document waited; "
                "nothing is wrong with the document, so upload it again"
            )
        return (
            "extractor_unavailable: the document extractor or embedding provider "
            f"was unavailable for longer than {hours:g} hours"
        )
    if reason == "lease_losses_exhausted":
        return (
            "worker_lost: processing was interrupted repeatedly (the worker "
            "stopped or ran out of memory while reading this document)"
        )
    return f"{reason}: {last_error or 'processing stopped'}"


async def on_file_process_dead(ctx: DeadContext) -> None:
    """The item gave up: the row says so, for these bytes only."""
    from app.modules.datastore.infrastructure.repositories.file_repository import (
        DatastoreFileRepository,
    )

    try:
        payload = FileProcessPayload.model_validate(ctx.item.payload)
    except ValueError:
        return
    files = DatastoreFileRepository(ctx.uow)
    last = await files.last_processing_error(payload.file_id)
    await files.mark_failed_permanent_for(
        payload.file_id,
        content_sha256=payload.sha256,
        error=dead_message(ctx.reason, last),
    )


async def run_file_cleanup(ctx: JobContext, payload: FileCleanupPayload) -> None:
    from app.modules.datastore.api.dependencies import build_file_service

    async with ctx.checkpoint() as uow:
        # Storage and the pod's index only; the service's repository is never
        # queried, and the session is released before any of the I/O.
        service = build_file_service(uow)
    await service.cleanup_deleted_paths(
        _pod_of(ctx.item.ns),
        is_folder=payload.is_folder,
        folder_prefix=payload.folder_prefix,
        files=[item.model_dump(mode="json") for item in payload.files],
    )


def register_file_work(
    registry: WorkRegistry,
    *,
    retry: RetryPolicy | None = None,
    lease: timedelta | None = None,
) -> None:
    """Register both kinds; a test passes its own registry and budgets."""
    registry.handler(
        kind=FILE_PROCESS,
        cls=WorkClass.INGESTION,
        mode=WorkMode.JOB,
        payload=FileProcessPayload,
        retry=retry or processing_retry(),
        lease=lease,
        concurrency=1,
        on_dead=on_file_process_dead,
    )(run_file_process)
    registry.handler(
        kind=FILE_CLEANUP,
        cls=WorkClass.INGESTION,
        mode=WorkMode.JOB,
        payload=FileCleanupPayload,
        lease=lease,
    )(run_file_cleanup)


register_file_work(default_registry)


async def enqueue_file_processing(
    session: AsyncSession,
    *,
    pod_id: UUID,
    file_id: UUID,
    sha256: str | None,
    delay: timedelta | None = None,
    registry: WorkRegistry | None = None,
) -> UUID | None:
    """Write ``file.process`` in ``session``'s transaction.

    Keyed by the bytes, so asking twice for the same revision is one item.
    """
    if datastore_settings.e2e_disable_worker_file_autoindex:
        return None
    return await WorkWriter(session, registry=registry).add(
        FILE_PROCESS,
        pod_namespace(pod_id),
        FileProcessPayload(file_id=file_id, sha256=sha256),
        dedup_key=f"file:{file_id}:{sha256 or ''}",
        concurrency_key=f"file:{file_id}",
        delay=delay,
    )


def content_update_delay() -> timedelta | None:
    seconds = max(0, datastore_settings.document_processing_debounce_seconds)
    return timedelta(seconds=seconds) if seconds else None


class _DeletionCleanup(Protocol):
    @property
    def is_folder(self) -> bool: ...
    @property
    def folder_prefix(self) -> str | None: ...
    @property
    def files(self) -> Sequence[Mapping[str, str]]: ...


def cleanup_payload(cleanup: _DeletionCleanup) -> FileCleanupPayload:
    return FileCleanupPayload(
        is_folder=cleanup.is_folder,
        folder_prefix=cleanup.folder_prefix,
        files=[CleanupFile.model_validate(dict(item)) for item in cleanup.files],
    )


async def add_file_cleanup_work(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    payload: FileCleanupPayload,
    registry: WorkRegistry | None = None,
) -> UUID | None:
    """Write ``file.cleanup`` in the delete's transaction."""
    return await WorkWriter(uow.session, registry=registry).add(
        FILE_CLEANUP, pod_namespace(pod_id), payload
    )
