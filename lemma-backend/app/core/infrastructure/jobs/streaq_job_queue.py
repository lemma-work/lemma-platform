"""Shared streaq job queue adapter."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime
import json
from typing import Any, Callable

from opentelemetry import trace
from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind
from streaq.task import Task, TaskStatus
from streaq.worker import Worker

from app.core.config import settings
from app.core.domain.job_queue import JobQueuePort
from app.core.log.log import get_logger
from app.core.request_context import current_observability_context

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

_JOB_CONTEXT_PREFIX = "lemma:observability:job-context:"
_JOB_CONTEXT_MIN_TTL_SECONDS = 48 * 60 * 60


def job_context_key(job_id: str) -> str:
    return f"{_JOB_CONTEXT_PREFIX}{job_id}"


def create_streaq_client(*, queue_name: str = "default") -> Worker[None]:
    """Create a lightweight streaq client for enqueuing and aborting tasks."""
    return Worker(
        redis_url=settings.redis_url,
        queue_name=queue_name,
        handle_signals=False,
    )


class SharedStreaqJobQueue(JobQueuePort):
    """Shared streaq-backed job queue for a process."""

    def __init__(self, worker_factory: Callable[[], Worker[Any]]):
        self._worker_factory = worker_factory
        self._worker = worker_factory()
        self._stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()

    def _reset_worker(self) -> None:
        self._worker = self._worker_factory()
        self._stack = None

    async def connect(self) -> Worker[Any]:
        """Initialize the shared streaq client if needed."""
        if self._stack is not None:
            return self._worker

        async with self._lock:
            if self._stack is None:
                # Never reuse a worker initialized in another lifespan context.
                if self._worker._initialized:  # noqa: SLF001
                    self._reset_worker()
                stack = AsyncExitStack()
                await stack.__aenter__()
                await stack.enter_async_context(self._worker)
                self._stack = stack

        return self._worker

    async def disconnect(self) -> None:
        """Close the shared streaq client when owned by this adapter."""
        stack = self._stack
        self._stack = None
        if stack is not None:
            try:
                await stack.aclose()
            except ValueError as exc:
                if "different Context" not in str(exc):
                    raise
                logger.debug(
                    "infrastructure.streaq_job_queue.ignoring_streaq_queue_shutdown_context.diagnostic"
                )
        self._reset_worker()

    async def enqueue(self, job_name: str, **kwargs: Any) -> Task[Any] | None:
        worker = await self.connect()
        task_id = kwargs.pop("_job_id", None)
        defer_until = kwargs.pop("_defer_until", None)
        task = worker.enqueue_unsafe(job_name, **kwargs)
        if task_id is not None:
            task.id = str(task_id)
        ttl_seconds = _JOB_CONTEXT_MIN_TTL_SECONDS
        if defer_until is not None:
            task.start(schedule=defer_until)
            now = (
                datetime.now(defer_until.tzinfo)
                if defer_until.tzinfo
                else datetime.now()
            )
            ttl_seconds = max(
                ttl_seconds,
                int((defer_until - now).total_seconds()) + _JOB_CONTEXT_MIN_TTL_SECONDS,
            )
        with tracer.start_as_current_span(
            "lemma.worker.enqueue",
            kind=SpanKind.PRODUCER,
            attributes={"lemma.task_name": job_name, "lemma.job_id": task.id},
        ):
            inherited = current_observability_context().as_transport()
            inject(inherited)
            if inherited:
                try:
                    await worker.redis.set(
                        job_context_key(task.id),
                        json.dumps(inherited, separators=(",", ":")),
                        ex=ttl_seconds,
                    )
                except Exception as exc:  # context never changes job semantics
                    logger.debug(
                        "worker.context.persist_failed",
                        job_id=task.id,
                        task_name=job_name,
                        error_type=type(exc).__name__,
                    )
            # streaq v7: awaiting the Task publishes it (Task.__await__ ->
            # _chain -> Worker.publish_task), applying schedule/TTL/priority.
            await task
        return task

    async def abort(self, job_id: str, *, timeout_seconds: float | None = None) -> bool:
        worker = await self.connect()
        return await worker.abort_by_id(job_id, timeout=timeout_seconds)

    def _task_job_key(self, task_id: str) -> str:
        return f"streaq:task-job:{task_id}"

    async def track_task_job(self, task_id: str, job_id: str) -> None:
        worker = await self.connect()
        await worker.redis.set(self._task_job_key(task_id), job_id)

    async def get_tracked_task_job_id(self, task_id: str) -> str | None:
        worker = await self.connect()
        job_id = await worker.redis.get(self._task_job_key(task_id))
        return str(job_id) if job_id else None

    async def clear_tracked_task_job(
        self,
        task_id: str,
        *,
        expected_job_id: str | None = None,
    ) -> None:
        worker = await self.connect()
        key = self._task_job_key(task_id)
        if expected_job_id is None:
            await worker.redis.delete([key])
            return

        current_job_id = await self.get_tracked_task_job_id(task_id)
        if current_job_id == expected_job_id:
            await worker.redis.delete([key])

    async def abort_tracked_task_job(
        self,
        task_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        job_id = await self.get_tracked_task_job_id(task_id)
        if not job_id:
            return False

        aborted = await self.abort(job_id, timeout_seconds=timeout_seconds)
        if aborted:
            await self.clear_tracked_task_job(task_id, expected_job_id=job_id)
        return aborted

    async def status(self, job_id: str) -> TaskStatus:
        worker = await self.connect()
        return await worker.status_by_id(job_id)

    async def defer(
        self,
        job_name: str,
        *,
        defer_until: datetime,
        **kwargs: Any,
    ) -> Task[Any] | None:
        kwargs["_defer_until"] = defer_until
        return await self.enqueue(job_name, **kwargs)


_job_queue: SharedStreaqJobQueue | None = None


def get_streaq_job_queue() -> SharedStreaqJobQueue:
    """Return the shared streaq queue adapter."""
    global _job_queue
    if _job_queue is None:
        _job_queue = SharedStreaqJobQueue(create_streaq_client)
    return _job_queue


async def close_streaq_job_queue() -> None:
    """Close the shared streaq queue adapter."""
    global _job_queue
    if _job_queue is None:
        return
    try:
        await _job_queue.disconnect()
    finally:
        _job_queue = None
