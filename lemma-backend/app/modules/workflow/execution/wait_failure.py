"""Failing the run that a wait belongs to.

Two callers need this now and they find the wait differently: the completion
path is told an external ref and looks the wait up by it, while the
reconciliation sweep already holds the wait it decided to expire. A ``HUMAN``
wait has no external ref at all -- a form is answered through the wait row
rather than by an outside system -- so it can only be failed this way.

Beside the engine rather than inside it for the reason ``underlying_work.py``
gives: the engine is at the architecture ratchet's file-size mark. It is part
of the engine's own implementation rather than a collaborator of it, which is
why it reaches the engine's terminal-event and announce steps directly instead
of taking them as ports -- both are engine bookkeeping that every terminal
transition performs identically, and duplicating either is how they drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.modules.workflow.domain.context import normalize_node_output
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.domain.workflow import WorkflowEntity

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from app.modules.workflow.execution.engine import WorkflowEngine


async def fail_run_for_wait(
    engine: "WorkflowEngine",
    wait: WorkflowRunWaitEntity,
    *,
    error: str,
    output: Mapping[str, object] | None = None,
) -> WorkflowRunEntity | None:
    """Fail the run this wait belongs to, and stop asking anyone about it.

    When the graph gives the waiting node (or a loop around it) a FAILURE
    edge, the step fails and the run carries on down that edge instead, in
    this transaction, and the live run is returned.

    Returns ``None`` when the run is already terminal, which is the ordinary
    outcome of a late completion event racing a cancellation.
    """
    run = await engine.run_repo.get_for_update(wait.run_id)
    if run is None or run.status not in (
        WorkflowRunStatus.WAITING,
        WorkflowRunStatus.RUNNING,
    ):
        return None
    # The wait was read before the lock was taken. A resume that held the lock
    # first may have completed it and moved the run on to another step, which
    # leaves the run live; failing it now would fail a run that went past this
    # wait. So the wait must still be the run's active one.
    current = await engine.wait_repo.get_active_for_run(wait.run_id)
    if current is None or current.id != wait.id:
        return None
    wait = current

    normalized = normalize_node_output(output)
    wait.fail(normalized or {"error": error})
    await engine.wait_repo.update(wait)
    flow = await engine.flow_repo.get(run.flow_id)
    if flow is not None and engine._stepper(None).divert_failure(
        run, flow, wait.node_id, error, output=normalized
    ):
        return await _carry_on(engine, run, flow, wait)
    if normalized:
        run.record_node_output(wait.node_id, {**normalized, "error": error})
    run.fail(error, node_id=wait.node_id)
    run = await engine.run_repo.update(run)
    # A failed run must not leave a question sitting in someone's inbox waiting
    # for an answer nobody will read -- the same reason `cancel_run` does this.
    # Only a form wait ever put one there.
    if wait.wait_type == WorkflowRunWaitType.HUMAN:
        await engine.form_questions.run_ended(pod_id=run.pod_id, run_id=run.id)
    await engine._record_transition(run)
    await engine._settle(run)
    return run


async def _carry_on(
    engine: "WorkflowEngine",
    run: WorkflowRunEntity,
    flow: WorkflowEntity,
    wait: WorkflowRunWaitEntity,
) -> WorkflowRunEntity:
    """Run the failure branch the stepper put the run on, as a resume would.

    The branch acts for the run's owner as of now, like any resumed step. An
    expired form's question is closed before the branch runs, so a form on the
    branch is the only one left asking.
    """
    if wait.wait_type == WorkflowRunWaitType.HUMAN:
        await engine.form_questions.run_ended(pod_id=run.pod_id, run_id=run.id)
    ctx = await owner_context(engine, run)
    token = set_current_context(ctx)
    try:
        result = await engine._stepper(ctx).advance(run, flow)
    finally:
        reset_current_context(token)
    run = await engine.run_repo.update(run)
    await engine._persist_wait(run, result)
    await engine._record_transition(run)
    await engine._settle(run)
    return run


async def owner_context(engine: "WorkflowEngine", run: WorkflowRunEntity) -> Context:
    return await create_authorization_data_service(engine.uow).build_user_context(
        user_id=run.user_id, pod_id=run.pod_id
    )
