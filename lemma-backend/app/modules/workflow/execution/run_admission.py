"""Holding runs back to a workflow's max_concurrent_runs, and letting them in.

A run started while its workflow is at the limit is not refused -- a schedule or
a webhook burst has nobody to tell -- and not dropped. It is created as usual
and parked at its entry node on a ``QUEUED`` wait, which does not count towards
the limit. Whenever a run of the workflow finishes, the oldest parked runs are
admitted into the places that frees, in the same transaction as the finish --
so a crash cannot leave a place free and a run parked behind it.

Both sides lock the workflow row, so two starts cannot both take the last place
and two finishes cannot both admit into it.

Beside the engine rather than inside it for the reason ``wait_failure.py``
gives: the engine is at the architecture ratchet's file-size mark, and this is
part of its own implementation, not a collaborator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus
from app.modules.workflow.domain.wait import (
    WaitRequest,
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.execution.stepper import StepResult

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from app.modules.workflow.execution.engine import WorkflowEngine

logger = get_logger(__name__)

#: How many parked runs one admission pass takes when no limit bounds it --
#: a limit that was removed while runs were parked.
UNBOUNDED_ADMISSION_BATCH = 100


async def queue_wait_if_full(
    engine: "WorkflowEngine", flow: WorkflowEntity, run: WorkflowRunEntity
) -> WaitRequest | None:
    """The wait that parks a new run, or None when it may start now."""
    if flow.max_concurrent_runs is None:
        return None
    # Locked so two starts cannot both see the last free place.
    await engine.flow_repo.get_for_update(flow.id)
    admitted = await engine.run_repo.count_admitted(flow.id, excluding=run.id)
    if admitted < flow.max_concurrent_runs:
        return None
    return WaitRequest(wait_type=WorkflowRunWaitType.QUEUED, external_ref=str(flow.id))


async def admit_queued_runs(engine: "WorkflowEngine", flow_id: UUID) -> int:
    """Start as many parked runs of a workflow as it has room for.

    In the caller's transaction, which is the one that freed the room; each
    admitted run is announced once it commits. Returns how many were admitted.
    One at a time and recounted after each, because an admitted run may end at
    once and free its place again.
    """
    unlocked = await engine.flow_repo.get(flow_id)
    if unlocked is None:
        return 0
    if (
        unlocked.max_concurrent_runs is None
        and not await engine.wait_repo.oldest_queued(flow_id, limit=1)
    ):
        # The common case: no limit and nobody parked. Not worth the lock that
        # would serialise every finish of the workflow.
        return 0
    flow = await engine.flow_repo.get_for_update(flow_id)
    if flow is None:
        return 0
    admitted = 0
    while admitted < UNBOUNDED_ADMISSION_BATCH:
        if flow.max_concurrent_runs is not None:
            room = flow.max_concurrent_runs - await engine.run_repo.count_admitted(
                flow.id
            )
            if room <= 0:
                break
        oldest = await engine.wait_repo.oldest_queued(flow.id, limit=1)
        if not oldest:
            break
        if await _admit_parked(engine, flow, oldest[0]):
            admitted += 1
    return admitted


async def _admit_parked(
    engine: "WorkflowEngine", flow: WorkflowEntity, queued: WorkflowRunWaitEntity
) -> bool:
    """Advance one parked run from its entry node; whether it was admitted."""
    run = await engine.run_repo.get_for_update(queued.run_id)
    if run is None or run.status != WorkflowRunStatus.RUNNING:
        # Cancelled or failed while parked; the wait is all that is left.
        queued.cancel()
        await engine.wait_repo.update(queued)
        return False
    queued.complete()
    await engine.wait_repo.update(queued)
    try:
        ctx = await engine._require_action(
            requester_user_id=run.user_id,
            action=Permissions.WORKFLOW_EXECUTE,
            pod_id=run.pod_id,
            flow_id=flow.id,
        )
    except DomainError as exc:
        # Checked when it started, but it has waited since: the person who
        # started it may no longer be allowed to run this workflow.
        run.fail(f"Could not start after waiting for a place: {exc}")
        result = StepResult()
    else:
        result = await engine._stepper(ctx).advance(run, flow)
    run = await engine.run_repo.update(run)
    await engine._persist_wait(run, result)
    # Not admitting again from inside the admission: the loop above recounts.
    await engine._record_transition(run, admit=False)
    engine.uow.after_commit(lambda: engine._announce(run))
    return True
