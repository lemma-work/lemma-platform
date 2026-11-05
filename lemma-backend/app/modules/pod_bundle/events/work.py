"""Pod-bundle jobs as work items: export, plan, the two fetches, apply, publish.

Each was a streaq task enqueued after the request had already committed the
job's state row, so a crash between the two left a QUEUED job nothing would
run, and the stale-job sweep failed it 40 minutes later. Now the request
writes the state row and the item in one transaction (``BundleJobQueue``), and:

* **admin class, JOB mode**, in the pod's namespace, one bundle job per pod at
  a time (``concurrency_key=pod_bundle:<pod>``);
* **dedup** keeps the job ids the streaq tasks used (``pod-export:<id>``,
  ``pod-import-plan:<id>`` for plan and both fetches, ``pod-import:<id>`` for
  apply, ``pod-publish:<id>``), so a second identical request still answers
  "already running";
* **checkpoints** are the jobs' own: apply commits each step and records it on
  the state row before the next, publish records each uploaded file. A worker
  that dies loses its lease and the next one resumes from the first pending
  step; one that is told to stop (SIGTERM) yields at the next step boundary
  (``yield_point``) and is resumed uncharged;
* **cancel** stays cooperative (the CANCELLING tombstone the handlers check
  between steps); a cancel that finds the item still queued drops it and ends
  the job in the same transaction (``ImportUseCases.cancel_import``);
* **dead** (attempts, lease losses, or the 30-minute cap a streaq job had)
  ends the job FAILED -- or CANCELLED if it was cancelling -- in the dead
  transaction, instead of leaving it "in progress" until the sweep.

The bodies live in ``handlers.py`` / ``publish_task.py`` and are imported when
an item runs, so the API (which only enqueues) does not load the exporter,
the GitHub publisher or the README model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    DeadContext,
    JobContext,
    Released,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
    registry as default_registry,
    was_database_unavailable,
)
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind, current_origin, origin_scope

logger = get_logger(__name__)

EXPORT = "pod_bundle.export"
PLAN = "pod_bundle.plan"
IMPORT_GITHUB = "pod_bundle.import_github"
IMPORT_URL = "pod_bundle.import_url"
APPLY = "pod_bundle.apply"
PUBLISH = "pod_bundle.publish"

#: Kinds that plan an import: one at a time per import, under one key.
PLANNING_KINDS = (PLAN, IMPORT_GITHUB, IMPORT_URL)

#: What a streaq job got (``JOB_TIMEOUT_SECONDS``). Apply is the long one: app
#: and function steps build in a sandbox.
MAX_RUNTIME = timedelta(minutes=30)
RETRY = RetryPolicy(max_attempts=3, max_lease_losses=3)


def export_key(export_id: UUID) -> str:
    return f"pod-export:{export_id}"


def plan_key(import_id: UUID) -> str:
    return f"pod-import-plan:{import_id}"


def apply_key(import_id: UUID) -> str:
    return f"pod-import:{import_id}"


def publish_key(publish_id: UUID) -> str:
    return f"pod-publish:{publish_id}"


def concurrency_key(pod_id: UUID) -> str:
    return f"pod_bundle:{pod_id}"


class _Carried(BaseModel):
    """How the request arrived, for the analytics the job raises.

    Export and publish run as whoever asked; the streaq jobs inherited that
    through their observability context, and an item carries it explicitly.
    """

    origin: str | None = None
    origin_platform: str | None = None

    def bound_origin(self) -> Origin | None:
        if not self.origin:
            return None
        try:
            return Origin(OriginKind(self.origin), platform=self.origin_platform)
        except ValueError:
            return None


def carried_origin() -> dict[str, str | None]:
    origin = current_origin()
    if origin is None:
        return {"origin": None, "origin_platform": None}
    return {"origin": origin.kind.value, "origin_platform": origin.platform}


class ExportJob(_Carried):
    export_id: UUID
    pod_id: UUID
    user_id: UUID


class ImportJob(BaseModel):
    import_id: UUID
    pod_id: UUID
    user_id: UUID


class GithubImportJob(ImportJob):
    owner: str | None = None
    repo: str | None = None


class UrlImportJob(ImportJob):
    source_kind: str
    source_id: UUID


class PublishJob(_Carried):
    publish_id: UUID
    pod_id: UUID
    user_id: UUID


@dataclass(frozen=True, slots=True)
class BundleWorkerContext:
    """What the job bodies need from the process: a unit-of-work factory."""

    uow_factory: SessionUnitOfWorkFactory


def worker_context() -> BundleWorkerContext:
    return BundleWorkerContext(SessionUnitOfWorkFactory(async_session_maker))


# --- the running item, for the bodies' yield points ---------------------------

_running: ContextVar[JobContext | None] = ContextVar("pod_bundle_job", default=None)


class _Yielding(BaseException):
    """A stopping worker unwinding a job body to its ``_run`` frame.

    A ``BaseException`` on purpose, like ``CancelledError``: the bodies end in
    ``except Exception`` blocks that mark the job FAILED, and a shutdown is not
    a failure. ``_run`` turns it into the runtime's ``Released``.
    """


def yield_point() -> None:
    """A step boundary: hand the job back now if the worker is stopping.

    Requeued, nothing charged; the next worker resumes from the state row. A
    no-op outside a work item (unit tests).
    """
    job = _running.get()
    if job is not None and job.stopping:
        raise _Yielding


type _Body = Callable[[dict[str, Any]], Awaitable[None]]


async def _run(
    ctx: JobContext, payload: BaseModel, body: _Body, origin: Origin | None
) -> None:
    token = _running.set(ctx)
    try:
        with origin_scope(origin):
            await body(payload.model_dump(mode="json"))
    except _Yielding:
        raise Released() from None
    finally:
        _running.reset(token)


_IMPORT = Origin(OriginKind.IMPORT)


async def run_export(ctx: JobContext, payload: ExportJob) -> None:
    from app.modules.pod_bundle.events.handlers import export_pod_bundle

    await _run(ctx, payload, export_pod_bundle, payload.bound_origin())


async def run_plan(ctx: JobContext, payload: ImportJob) -> None:
    from app.modules.pod_bundle.events.handlers import plan_pod_import

    await _run(ctx, payload, plan_pod_import, _IMPORT)


async def run_import_github(ctx: JobContext, payload: GithubImportJob) -> None:
    from app.modules.pod_bundle.events.handlers import import_pod_github

    await _run(ctx, payload, import_pod_github, _IMPORT)


async def run_import_url(ctx: JobContext, payload: UrlImportJob) -> None:
    from app.modules.pod_bundle.events.handlers import import_pod_url

    await _run(ctx, payload, import_pod_url, _IMPORT)


async def run_apply(ctx: JobContext, payload: ImportJob) -> None:
    from app.modules.pod_bundle.events.handlers import apply_pod_import

    await _run(ctx, payload, apply_pod_import, _IMPORT)


async def run_publish(ctx: JobContext, payload: PublishJob) -> None:
    from app.modules.pod_bundle.events.publish_task import publish_pod_github

    await _run(ctx, payload, publish_pod_github, payload.bound_origin())


# --- dead ---------------------------------------------------------------------

_JOB_ID_FIELDS = ("export_id", "import_id", "publish_id")


async def end_dead_job(ctx: DeadContext) -> None:
    """The item is dead: end its job so nobody watches it "in progress"."""
    from app.modules.pod_bundle.domain.state import PublishState
    from app.modules.pod_bundle.infrastructure.realtime import (
        error_payload,
        publish_bundle_event,
    )
    from app.modules.pod_bundle.infrastructure.state_store import (
        get_pod_bundle_state_store,
    )

    job_id = next(
        UUID(str(ctx.item.payload[field]))
        for field in _JOB_ID_FIELDS
        if ctx.item.payload.get(field)
    )
    state = await get_pod_bundle_state_store().terminalize_interrupted(
        ctx.uow,
        job_id,
        database_unavailable=ctx.reason == "unavailable_exhausted"
        and was_database_unavailable(ctx.last_error),
    )
    logger.warning(
        "pod_bundle.job.dead",
        kind=ctx.item.kind,
        job_id=str(job_id),
        reason=ctx.reason,
        terminalized=state is not None,
    )
    if state is None:
        return
    ended = state

    async def announce() -> None:
        await publish_bundle_event(
            job_id, error_payload(ended.error or "Job interrupted.", ended.seq)
        )
        if isinstance(ended, PublishState) and ended.publish_lock_key is not None:
            from app.modules.pod_bundle.infrastructure.publish_lock import (
                get_publish_concurrency_lock,
            )

            held = ended.publish_lock_key
            await get_publish_concurrency_lock().release(
                key=held, owner=ended.publish_id
            )

    ctx.uow.after_commit(announce)


def register_bundle_work(registry: WorkRegistry) -> None:
    for kind, payload, handler in (
        (EXPORT, ExportJob, run_export),
        (PLAN, ImportJob, run_plan),
        (IMPORT_GITHUB, GithubImportJob, run_import_github),
        (IMPORT_URL, UrlImportJob, run_import_url),
        (APPLY, ImportJob, run_apply),
        (PUBLISH, PublishJob, run_publish),
    ):
        registry.handler(
            kind=kind,
            cls=WorkClass.ADMIN,
            mode=WorkMode.JOB,
            payload=payload,
            retry=RETRY,
            concurrency=1,
            on_dead=end_dead_job,
            max_runtime=MAX_RUNTIME,
        )(handler)


register_bundle_work(default_registry)


# --- the writer the use cases hold ------------------------------------------


class BundleJobQueue:
    """Writes a bundle job's item on the request's open transaction."""

    async def queue_job(
        self,
        uow: SqlAlchemyUnitOfWork,
        kind: str,
        payload: BaseModel,
        *,
        pod_id: UUID,
        dedup_key: str,
    ) -> bool:
        """False when an identical job is already queued (the request conflicts)."""
        item = await WorkWriter.of(uow).add(
            kind,
            pod_namespace(pod_id),
            payload,
            dedup_key=dedup_key,
            concurrency_key=concurrency_key(pod_id),
        )
        return item is not None

    async def drop_queued(
        self,
        uow: SqlAlchemyUnitOfWork,
        kinds: tuple[str, ...],
        *,
        pod_id: UUID,
        dedup_key: str,
    ) -> bool:
        """Remove a still-queued item; True if one was, False if running/none."""
        writer = WorkWriter.of(uow)
        dropped = False
        for kind in kinds:
            outcome = await writer.cancel(kind, pod_namespace(pod_id), dedup_key)
            if outcome == "running":
                return False
            dropped = dropped or outcome == "dropped"
        return dropped


__all__ = [
    "APPLY",
    "EXPORT",
    "IMPORT_GITHUB",
    "IMPORT_URL",
    "PLAN",
    "PLANNING_KINDS",
    "PUBLISH",
    "BundleJobQueue",
    "ExportJob",
    "GithubImportJob",
    "ImportJob",
    "PublishJob",
    "UrlImportJob",
    "apply_key",
    "carried_origin",
    "export_key",
    "plan_key",
    "publish_key",
    "register_bundle_work",
    "yield_point",
]
