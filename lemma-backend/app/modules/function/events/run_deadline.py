"""``function.deadline``: end a run whose execution window has closed.

Written in the transaction that starts a run (``run_at`` = its execution
deadline plus the callback grace), and in the one that creates a synchronous
run (its start deadline plus the grace), because nothing else settles a run
whose process died: a JOB function finishes by callback after its work item is
gone, and a synchronous call dies with the API request that held it.

A FAST handler: one transaction, the run row locked, no network. A run already
terminal is a no-op; one whose deadline moved later -- a synchronous run that
started after this item was written -- re-arms the item for the new time.
Replaces the once-a-minute ``reconcile_function_runs`` expiry sweep.

The module imports no network client, which ``fast_guard`` checks; the
repository is imported inside the handler because it imports this module to
write the item.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work import (
    FastContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
    registry as default_registry,
)
from app.modules.function.application.runtime_policy import (
    FUNCTION_JOB_CALLBACK_GRACE_SECONDS,
)

FUNCTION_DEADLINE = "function.deadline"

#: How long past a run's deadline anything may still be settling it: the
#: runtime's callback for a JOB function, the dispatcher's own timeout for a
#: synchronous one. The item fires after it, so it never races either.
DEADLINE_GRACE = timedelta(seconds=FUNCTION_JOB_CALLBACK_GRACE_SECONDS)


class FunctionDeadline(BaseModel):
    run_id: UUID


def deadline_dedup_key(run_id: UUID, due: datetime) -> str:
    """One item per (run, moment): re-arming for the same time coalesces."""
    return f"function-deadline:{run_id}:{due.astimezone(UTC).isoformat()}"


async def add_function_deadline(
    session: AsyncSession,
    *,
    pod_id: UUID,
    run_id: UUID,
    deadline_at: datetime,
    registry: WorkRegistry | None = None,
) -> None:
    """Write the item that ends ``run_id`` if it is still open after ``deadline_at``."""
    due = deadline_at + DEADLINE_GRACE
    await WorkWriter(session, registry=registry).add(
        FUNCTION_DEADLINE,
        pod_namespace(pod_id),
        FunctionDeadline(run_id=run_id),
        run_at=due,
        dedup_key=deadline_dedup_key(run_id, due),
    )


async def end_overdue_run(ctx: FastContext, payload: FunctionDeadline) -> None:
    from app.modules.function.infrastructure.execution_repository import (
        FunctionExecutionRepository,
    )

    later = await FunctionExecutionRepository(ctx.uow).fail_if_overdue(
        payload.run_id, now=datetime.now(UTC), grace=DEADLINE_GRACE
    )
    if later is not None:
        await ctx.work.add(
            FUNCTION_DEADLINE,
            ctx.item.ns,
            payload,
            run_at=later,
            dedup_key=deadline_dedup_key(payload.run_id, later),
        )


def register_function_deadline(registry: WorkRegistry) -> None:
    registry.handler(
        kind=FUNCTION_DEADLINE,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=FunctionDeadline,
        # Nothing here depends on anything but the row; a failure is the
        # database failing, and it is retried like any other.
        retry=RetryPolicy(max_attempts=5),
    )(end_overdue_run)


register_function_deadline(default_registry)
