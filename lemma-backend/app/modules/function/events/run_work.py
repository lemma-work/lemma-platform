"""Queued function runs as work items: ``function.run`` and ``function.cancel``.

``function.run`` is written in the transaction that creates an asynchronous
run, so a committed PENDING run always has the work that starts it -- the
post-commit enqueue it replaces could be lost, which is what the
once-a-minute ``reconcile_function_runs`` sweep existed to repair.

The job starts the run and invokes the sandbox. What it signals the runtime:

* the pod already runs its cap of functions -> ``Defer``: requeued, nothing
  charged. The slot is the RUNNING row, counted under the pod's lock at the
  start, so it is held until the run is terminal however that end arrives;
* the sandbox cannot be reached or is still starting, before anything ran ->
  ``Unavailable``: backoff, no attempt charged, bounded by the run's own start
  window;
* anything after the start is the run's outcome, written to the row -- a
  runtime error is a FAILED run, never a retry, because the function may have
  done its work.

A lease that expires after the start never invokes again: the next claim finds
the run RUNNING and leaves it to its callback or to ``function.deadline``.

``function.cancel`` asks the sandbox to stop a run that was cancelled in the
database (a workflow cancelled or its step expired). Best effort, and the
run's state does not depend on it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work import (
    was_database_unavailable,
    DeadContext,
    Defer,
    Exponential,
    JobContext,
    PermanentFailure,
    RetryPolicy,
    Unavailable,
    UnavailableEvidence,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
    registry as default_registry,
)
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.function.config import function_settings
from app.modules.function.events import run_deadline as _run_deadline  # noqa: F401  registers function.deadline

if TYPE_CHECKING:
    from app.modules.function.application.function_dispatcher import (
        FunctionDispatcher,
    )

FUNCTION_RUN = "function.run"
FUNCTION_CANCEL = "function.cancel"

#: How soon a run turned away by its pod's cap asks again. Short: the slot it
#: wants frees whenever any run of the pod ends, and asking costs one count.
CAPACITY_RETRY = timedelta(seconds=5)


class FunctionRunWork(BaseModel):
    run_id: UUID


def run_retry() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=3,
        max_lease_losses=3,
        # Never longer than the run may wait to start: past that the run fails
        # as never started on its next claim anyway.
        unavailable_budget=timedelta(
            seconds=function_settings.function_queued_start_deadline_seconds
        ),
        backoff=Exponential(
            base=timedelta(seconds=5), cap=timedelta(minutes=2), jitter=0.2
        ),
    )


async def add_function_run_work(
    session: AsyncSession,
    *,
    pod_id: UUID,
    run_id: UUID,
    registry: WorkRegistry | None = None,
) -> None:
    """Write ``function.run`` in ``session``'s transaction; one per run."""
    await WorkWriter(session, registry=registry).add(
        FUNCTION_RUN,
        pod_namespace(pod_id),
        FunctionRunWork(run_id=run_id),
        dedup_key=f"function-run:{run_id}",
    )


async def add_function_cancel(
    session: AsyncSession,
    *,
    pod_id: UUID,
    run_id: UUID,
    registry: WorkRegistry | None = None,
) -> None:
    await WorkWriter(session, registry=registry).add(
        FUNCTION_CANCEL,
        pod_namespace(pod_id),
        FunctionRunWork(run_id=run_id),
        dedup_key=f"function-cancel:{run_id}",
    )


#: Builds the dispatcher a job drives, from the job's checkpoint factory. The
#: deployment's is ``api.dependencies.build_function_dispatcher``; a test
#: hands its own, with the sandbox seam replaced and nothing else.
DispatcherFactory = Callable[[UnitOfWorkFactory], "FunctionDispatcher"]


def _deployment_dispatcher(uow_factory: UnitOfWorkFactory) -> FunctionDispatcher:
    from app.modules.function.api.dependencies import build_function_dispatcher

    return build_function_dispatcher(uow_factory)


def _run_handler(build: DispatcherFactory):
    async def run_queued_function(ctx: JobContext, payload: FunctionRunWork) -> None:
        from app.modules.function.application.dispatch_failures import (
            PodAtCapacity,
            SandboxNotReady,
        )

        dispatcher = build(ctx.checkpoint)
        try:
            await dispatcher.run_queued(
                payload.run_id, before_start=ctx.raise_if_stopping
            )
        except PodAtCapacity:
            raise Defer(CAPACITY_RETRY) from None
        except SandboxNotReady as exc:
            raise Unavailable(
                str(exc), evidence=UnavailableEvidence.HEALTH_CHECK_FAILING
            ) from exc
        except LookupError as exc:
            raise PermanentFailure(str(exc)) from exc

    return run_queued_function


def _cancel_handler(build: DispatcherFactory):
    async def cancel_in_runtime(ctx: JobContext, payload: FunctionRunWork) -> None:
        await build(ctx.checkpoint).cancel_in_runtime(payload.run_id)

    return cancel_in_runtime


def dead_message(reason: str, last_error: str | None) -> str:
    """What a person reads on a queued run that could not be started."""
    if reason == "unavailable_exhausted" and was_database_unavailable(last_error):
        return (
            "Function never started: Lemma's database was unavailable until "
            "the run's start deadline. Nothing ran, so it is safe to retry."
        )
    if reason == "unavailable_exhausted":
        return (
            "Function never started: its sandbox could not be reached before "
            "the run's start deadline. Nothing ran, so it is safe to retry."
        )
    if reason == "lease_losses_exhausted":
        return (
            "Function never started: the worker starting it stopped repeatedly. "
            "Nothing ran, so it is safe to retry."
        )
    return f"Function could not be started ({reason}): {last_error or 'unknown'}"


async def on_function_run_dead(ctx: DeadContext) -> None:
    """The start gave up: a run that is still PENDING says so.

    A run that started is left alone -- its callback or its deadline item ends
    it, and failing it here would contradict whatever the runtime reports.
    """
    from app.modules.function.infrastructure.execution_repository import (
        FunctionExecutionRepository,
    )

    try:
        payload = FunctionRunWork.model_validate(ctx.item.payload)
    except ValueError:
        return
    await FunctionExecutionRepository(ctx.uow).fail_pending(
        payload.run_id, error=dead_message(ctx.reason, ctx.last_error)
    )


def register_function_run_work(
    registry: WorkRegistry,
    *,
    retry: RetryPolicy | None = None,
    lease: timedelta | None = None,
    dispatcher_factory: DispatcherFactory | None = None,
) -> None:
    """Register both kinds; a test passes its own registry, budgets and sandbox."""
    build = dispatcher_factory or _deployment_dispatcher
    registry.handler(
        kind=FUNCTION_RUN,
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=FunctionRunWork,
        retry=retry or run_retry(),
        lease=lease,
        on_dead=on_function_run_dead,
    )(_run_handler(build))
    registry.handler(
        kind=FUNCTION_CANCEL,
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=FunctionRunWork,
        retry=RetryPolicy(max_attempts=2, unavailable_budget=timedelta(minutes=5)),
        lease=lease,
    )(_cancel_handler(build))


register_function_run_work(default_registry)
