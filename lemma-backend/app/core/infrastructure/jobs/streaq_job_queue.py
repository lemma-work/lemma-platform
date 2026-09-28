"""Shared streaq job queue adapter."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import partial
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
from app.core.infrastructure.jobs.lanes import Lane, lane_queue_name
from app.core.log.log import get_logger
from app.core.origin import current_origin
from app.core.request_context import (
    create_background_task,
    current_observability_context,
)

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

_JOB_CONTEXT_PREFIX = "lemma:observability:job-context:"
_JOB_CONTEXT_MIN_TTL_SECONDS = 48 * 60 * 60


def job_context_key(job_id: str) -> str:
    return f"{_JOB_CONTEXT_PREFIX}{job_id}"


async def load_job_observability_context(redis, job_id: str) -> dict[str, str]:
    """Best-effort read of the rolling-deployment-compatible sidecar.

    Beside the ``enqueue`` that writes it and the key builder it is written
    under, so the two halves of one Redis key cannot drift apart in separate
    files.
    """
    try:
        raw = await redis.get(job_context_key(job_id))
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in parsed.items()
            if isinstance(key, str) and isinstance(value, str | int)
        }
    except Exception:
        # Still best-effort -- correlation is never worth failing a job over --
        # but not silent. A sidecar that has stopped being readable makes every
        # job in the deployment lose its parent trace, and the symptom of that
        # is an absence, which nobody notices.
        logger.warning(
            "infrastructure.streaq_job_queue.job_context_read_failed.degraded",
            job_id=job_id,
            exc_info=True,
        )
        return {}


def create_streaq_client(*, queue_name: str = "default") -> Worker[None]:
    """Create a lightweight streaq client for enqueuing and aborting tasks."""
    return Worker(
        redis_url=settings.redis_url,
        queue_name=queue_name,
        handle_signals=False,
    )


@dataclass(slots=True)
class _OpenClients:
    """One generation of streaq clients, and the task that holds them open.

    ``owner`` and ``ready`` are ``None`` only for clients handed in already open
    (tests).
    """

    primary: Worker[Any]
    lanes: dict[str, Worker[None]]
    owner: asyncio.Task[None] | None = None
    ready: asyncio.Future[None] | None = None
    release: asyncio.Event = field(default_factory=asyncio.Event)

    def alive(self) -> bool:
        if self.owner is None:
            return True
        return not self.owner.done() and not self.release.is_set()


class SharedStreaqJobQueue(JobQueuePort):
    """Shared streaq-backed job queue for a process.

    Lanes are separate Redis queues, so publishing needs a client per lane. The
    interactive client also backs the lane-independent plain-Redis helpers
    below.

    **Every client is opened and closed on one dedicated owner task**, never on
    the task that happens to need it first. A streaq client is a coredis
    connection pool, and a coredis pool is an anyio task group: every new
    connection runs as a child of it, and the group belongs to whichever task
    entered it. The bulk-lane client used to be entered lazily, from the first
    enqueue -- which in the worker is a streaq job running under its own
    timeout scope. When that scope fired, the pool's group was cancelled with
    it. From then on every new connection, from every task in the process,
    failed with a ``CancelledError`` none of them had been sent: it killed the
    bulk lane's worker outright, and it killed the ``datastore-file-events``
    stream reader that enqueued next, which FastStream does not restart after a
    cancellation. Both stayed dead for hours with every health check green.

    Owned by a task of its own, the pool's lifetime is the owner's and nothing
    else's. A caller cancelled while the clients are opening leaves them
    opening for the next caller; and if the owner does die -- the pool itself
    failed -- the next caller opens a fresh generation instead of inheriting a
    poisoned one.
    """

    def __init__(self, worker_factory: Callable[[], Worker[Any]]):
        self._worker_factory = worker_factory
        self._clients: _OpenClients | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Whether a generation of clients is open and its owner still alive."""
        return self._clients is not None and self._clients.alive()

    async def _lane_client(self, job_name: str) -> Worker[Any]:
        """Client for the lane ``job_name`` is registered on."""
        from app.core.infrastructure.jobs.streaq_runtime import lane_for_task

        lane = lane_for_task(job_name)
        clients = await self._open_clients()
        if lane is Lane.INTERACTIVE:
            return clients.primary
        return clients.lanes[lane_queue_name(lane)]

    async def _all_clients(self) -> list[Worker[Any]]:
        """Every open client, for id-keyed lookups whose lane is unknown."""
        clients = await self._open_clients()
        return [clients.primary, *clients.lanes.values()]

    async def connect(self) -> Worker[Any]:
        """Open every lane's client, if they are not open, and return the default."""
        return (await self._open_clients()).primary

    async def _open_clients(self) -> _OpenClients:
        clients = self._clients
        if clients is None or not clients.alive():
            async with self._lock:
                clients = self._clients
                if clients is None or not clients.alive():
                    if clients is not None and not clients.release.is_set():
                        # The owner ended without being asked to: the pool
                        # failed under it. Its traceback is already on record as
                        # `background_task.failed`; this says what happens next.
                        logger.error(
                            "infrastructure.streaq_job_queue.clients_lost.failed",
                        )
                    clients = self._start_owner()
                    # Recorded before the handshake, so a caller cancelled while
                    # waiting cannot orphan this generation and send the next
                    # caller off to open a second one over the same clients.
                    self._clients = clients
        if clients.ready is not None:
            # Shielded: one caller being cancelled must not cancel the handshake
            # every other caller is waiting on. If the owner failed to open, its
            # error is raised here -- and the next call starts afresh.
            await asyncio.shield(clients.ready)
        return clients

    def _start_owner(self) -> _OpenClients:
        # Fresh clients for every generation: one entered by an earlier owner,
        # or still being entered by one, must never be entered again.
        clients = _OpenClients(
            primary=self._worker_factory(),
            lanes={
                lane_queue_name(lane): create_streaq_client(
                    queue_name=lane_queue_name(lane)
                )
                for lane in Lane
                if lane is not Lane.INTERACTIVE
            },
            ready=asyncio.get_running_loop().create_future(),
        )
        clients.owner = create_background_task(
            _hold_clients_open(clients), name="streaq-job-queue-clients"
        )
        clients.owner.add_done_callback(partial(_settle_ready, clients))
        return clients

    async def disconnect(self) -> None:
        """Close every client, on the task that opened them."""
        clients, self._clients = self._clients, None
        if clients is None or clients.owner is None:
            return
        owner = clients.owner
        clients.release.set()
        if owner.get_loop() is not asyncio.get_running_loop():
            # A test that ran the lifespan on a loop that has since closed.
            # Nothing on this loop can wait on that task.
            logger.debug(
                "infrastructure.streaq_job_queue.ignoring_streaq_queue_shutdown_context.diagnostic"
            )
            return
        done, _ = await asyncio.wait({owner}, timeout=_CLOSE_TIMEOUT_SECONDS)
        if not done:
            owner.cancel()
            await asyncio.wait({owner}, timeout=_CLOSE_TIMEOUT_SECONDS)

    async def enqueue(self, job_name: str, **kwargs: Any) -> Task[Any] | None:
        worker = await self._lane_client(job_name)
        task_id = kwargs.pop("_job_id", None)
        defer_until = kwargs.pop("_defer_until", None)
        task = worker.enqueue_unsafe(job_name, **kwargs)
        if task_id is not None:
            task.id = str(task_id)
        ttl_seconds = _JOB_CONTEXT_MIN_TTL_SECONDS
        if defer_until is not None:
            task.start(schedule=defer_until)
            now = datetime.now(defer_until.tzinfo)
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
            # Origin travels on the sidecar, not in `ObservabilityContext`: that
            # object's fields become log fields on every line, and `origin` is
            # already spoken for in the logging catalog. A job enqueued by a
            # schedule must still look like SCHEDULE work when it runs, minutes
            # later, in a different process.
            origin = current_origin()
            if origin is not None:
                inherited["origin"] = origin.kind.value
                if origin.platform:
                    inherited["origin_platform"] = origin.platform
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
        # A job id does not carry its lane, and streaq scopes these lookups by
        # queue, so ask each open lane and take the first that owns it.
        for worker in await self._all_clients():
            if await worker.abort_by_id(job_id, timeout=timeout_seconds):
                return True
        return False

    async def status(self, job_id: str) -> TaskStatus:
        # As with abort: the id is lane-agnostic, so consult each open lane and
        # return the first that actually knows this job.
        clients = await self._all_clients()
        result = TaskStatus.NOT_FOUND
        for worker in clients:
            result = await worker.status_by_id(job_id)
            if result != TaskStatus.NOT_FOUND:
                return result
        return result

    async def defer(
        self,
        job_name: str,
        *,
        defer_until: datetime,
        **kwargs: Any,
    ) -> Task[Any] | None:
        kwargs["_defer_until"] = defer_until
        return await self.enqueue(job_name, **kwargs)


#: How long ``disconnect`` waits for the owner task to close the pools.
_CLOSE_TIMEOUT_SECONDS = 5.0


async def _hold_clients_open(clients: _OpenClients) -> None:
    """Enter every client, report readiness, and hold them open until released.

    Runs as its own task, so the pools' task groups are anchored here and
    nowhere else -- see ``SharedStreaqJobQueue``. A failure to open is handed
    to the waiters by ``_settle_ready`` when this task ends.
    """
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(clients.primary)
        for client in clients.lanes.values():
            await stack.enter_async_context(client)
        if clients.ready is not None and not clients.ready.done():
            clients.ready.set_result(None)
        await clients.release.wait()


def _settle_ready(clients: _OpenClients, owner: asyncio.Task[None]) -> None:
    """Hand an owner that ended before it was ready to everyone waiting on it.

    Never as a cancellation: the waiters were not cancelled, and a
    CancelledError that reaches a stream reader ends it for good.
    """
    ready = clients.ready
    if ready is None or ready.done():
        return
    error = None if owner.cancelled() else owner.exception()
    ready.set_exception(error or ConnectionError("streaq clients stopped opening"))


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
