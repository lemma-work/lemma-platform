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

from app.modules.workflow.domain.context import normalize_node_output
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)

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

    Returns ``None`` when the run is already terminal, which is the ordinary
    outcome of a late completion event racing a cancellation.
    """
    run = await engine.run_repo.get_for_update(wait.run_id)
    if run is None or run.status not in (
        WorkflowRunStatus.WAITING,
        WorkflowRunStatus.RUNNING,
    ):
        return None

    normalized = normalize_node_output(output)
    wait.fail(normalized or {"error": error})
    await engine.wait_repo.update(wait)
    if normalized:
        run.record_node_output(wait.node_id, {**normalized, "error": error})
    run.fail(error, node_id=wait.node_id)
    run = await engine.run_repo.update(run)
    # A failed run must not leave a question sitting in someone's inbox waiting
    # for an answer nobody will read -- the same reason `cancel_run` does this.
    # Only a form wait ever put one there.
    if wait.wait_type == WorkflowRunWaitType.HUMAN:
        await engine.notification_adapter.cancel_for_run(run_id=run.id)
    engine._collect_terminal_event(run)
    await engine.uow.commit()
    await engine._announce(run)
    return run
