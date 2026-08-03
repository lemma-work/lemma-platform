"""Stopping the agent or function a suspended run was waiting on.

Two callers must agree on this: cancelling a run, and the reconciliation sweep
expiring one that outlived its ceiling. A run failed for hanging whose agent is
still running is exactly the sandbox burn the ceiling exists to end, so expiry
takes the same path cancel does.

It lives beside the engine rather than inside it because it is a composition
concern — it drives the agent and function ports and touches no run state — and
because the engine is already at the architecture ratchet's file-size mark.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.workflow.domain.ports import AgentPort, FunctionPort
from app.modules.workflow.domain.run import WorkflowRunEntity
from app.modules.workflow.domain.wait import WorkflowRunWaitEntity, WorkflowRunWaitType

logger = get_logger(__name__)


async def stop_underlying_work(
    wait: WorkflowRunWaitEntity,
    *,
    run: WorkflowRunEntity,
    agent_adapter: AgentPort,
    function_adapter: FunctionPort,
) -> None:
    """Stop the work this wait is holding open.

    Best effort by design: the agent or function may finish in the same instant,
    and a failure here must still let the caller cancel or fail the run — the
    late completion event finds no ACTIVE wait and is dropped either way. What
    this prevents is an agent burning tokens for an hour on an answer that was
    discarded the moment it was asked for.
    """
    if wait.external_ref is None:
        return
    try:
        if wait.wait_type == WorkflowRunWaitType.AGENT:
            await agent_adapter.stop_conversation(UUID(wait.external_ref), run.user_id)
        elif wait.wait_type == WorkflowRunWaitType.FUNCTION:
            await function_adapter.cancel_run(UUID(wait.external_ref))
    except Exception:
        logger.warning(
            "workflow.cancel.underlying_work_stop_failed",
            run_id=str(run.id),
            wait_type=wait.wait_type.value,
            exc_info=True,
        )


async def stop_underlying_work_for_wait(
    wait: WorkflowRunWaitEntity,
    *,
    run_repo,
    agent_adapter: AgentPort,
    function_adapter: FunctionPort,
) -> None:
    """Same, for a caller holding a wait but not its run (the expiry sweep)."""
    run = await run_repo.get(wait.run_id)
    if run is None:
        return
    await stop_underlying_work(
        wait,
        run=run,
        agent_adapter=agent_adapter,
        function_adapter=function_adapter,
    )
