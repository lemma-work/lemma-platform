"""Resuming and reconciling workflow runs parked on an AGENT step.

Every other wait is woken or expired by the work items written with it
(``execution/wait_work.py``). An AGENT wait is not yet: the agent's finalize
does not emit facts until the agent module moves onto the work runtime, so its
completion still arrives through the ``workflow-agent-events`` group, and a lost
one is found by polling the conversation here. This module goes with them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.log.log import get_logger
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.execution.wait_ceiling import ceiling_seconds, expiry_error

logger = get_logger(__name__)

# A wait this old with no completion event is checked against the source of
# truth by the reconciliation sweep.
RECONCILE_AFTER = timedelta(minutes=10)
RECONCILE_BATCH = 100
# Inside the sweep's five-minute interval, so ticks never overlap.
RECONCILE_BUDGET_SECONDS = 120.0

# Agent wait reasons that resolve themselves. A whitelist on purpose: a wait
# reason added later is subject to the ceiling until someone decides otherwise,
# because the failure mode of guessing wrong in the other direction is a ceiling
# that silently stops applying.
SELF_RESOLVING_WAIT_REASONS = frozenset({"WAIT"})

_AGENT = WorkflowRunWaitType.AGENT


class RunResumeService:
    """Handles agent conversations a run waits on."""

    def __init__(self, engine: WorkflowEngine):
        self._engine = engine
        self._uow = engine.uow

    async def resume_for_agent_conversation(self, *, conversation_id: str) -> bool:
        """Resume or fail the run waiting on an agent conversation."""
        wait = await self._engine.wait_repo.find_active_by_external_ref(
            _AGENT, conversation_id
        )
        if wait is None:
            logger.debug(
                "workflow.run_resume_service.no_active_workflow_wait_agent.observed",
                conversation_id=conversation_id,
            )
            return False

        agent_status = await self._engine.agent_adapter.get_conversation_status(
            UUID(conversation_id)
        )
        return await self._apply_agent_status(wait, conversation_id, agent_status)

    async def reconcile_stale_waits(self) -> int:
        """Self-heal AGENT waits whose completion event was lost; expire hung ones.

        Every ACTIVE AGENT wait older than RECONCILE_AFTER is checked against
        the conversation: finished work resumes the run, failed or vanished
        work fails it, in-progress work is left alone -- unless it has outlived
        its ceiling, which fails the run and stops the agent. Paged by
        ``(created_at, id)`` within RECONCILE_BUDGET_SECONDS. Returns the
        number of waits acted on.
        """
        now = datetime.now(timezone.utc)
        started = time.monotonic()
        after: tuple[datetime, UUID] | None = None
        acted = 0
        while True:
            waits = await self._engine.wait_repo.list_active_older_than(
                wait_types=[_AGENT],
                created_before=now - RECONCILE_AFTER,
                limit=RECONCILE_BATCH,
                after=after,
            )
            for wait in waits:
                if await self._reconcile_one(wait, now=now):
                    acted += 1
            if len(waits) < RECONCILE_BATCH:
                break
            if time.monotonic() - started >= RECONCILE_BUDGET_SECONDS:
                logger.warning("workflow.reconcile.budget_exhausted", acted=acted)
                break
            last = waits[-1]
            assert last.created_at is not None
            after = (last.created_at, last.id)
        if acted:
            logger.debug("workflow.reconcile.recovered", count=acted)
        return acted

    async def _reconcile_one(
        self, wait: WorkflowRunWaitEntity, *, now: datetime
    ) -> bool:
        """Act on one stale wait if its conversation says to; say if it did."""
        if not wait.external_ref:
            return False
        try:
            agent_status = await self._engine.agent_adapter.get_conversation_status(
                UUID(wait.external_ref)
            )
            if await self._expire_overdue_wait(
                wait, now=now, agent_status=agent_status
            ):
                return True
            return await self._apply_agent_status(
                wait, wait.external_ref, agent_status, reconciled=True
            )
        except Exception:
            logger.error(
                "workflow.reconcile.failed",
                wait_id=str(wait.id),
                run_id=str(wait.run_id),
                exc_info=True,
            )
            # Nothing this wait did was committed. Without the rollback a
            # database error leaves the shared session unusable, and every
            # wait after it in the sweep fails the same way.
            await self._uow.rollback()
            return False

    async def _expire_overdue_wait(
        self,
        wait: WorkflowRunWaitEntity,
        *,
        now: datetime,
        agent_status: dict[str, object],
    ) -> bool:
        """Fail the run when its agent wait has outlived the ceiling.

        A waiting agent (``wait_reason`` WAIT) wakes itself and is healthy, so
        it is exempt; one blocked on a person gets the human ceiling.
        """
        wait_reason = agent_status.get("wait_reason")
        if wait_reason in SELF_RESOLVING_WAIT_REASONS:
            return False
        created_at = wait.created_at
        if created_at is None:
            return False
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        seconds = ceiling_seconds(wait, human_paced=wait_reason == "HUMAN")
        if seconds is None or created_at > now - timedelta(seconds=seconds):
            return False
        logger.warning(
            "workflow.reconcile.wait_expired",
            run_id=str(wait.run_id),
            wait_id=str(wait.id),
            wait_type=wait.wait_type.value,
        )
        # Stop the work before failing the run: without it a hung agent keeps
        # burning a sandbox after the run waiting on it is marked failed.
        await self._engine.stop_underlying_work(wait)
        await self._engine.fail_for_wait(wait, error=expiry_error(wait, seconds))
        return True

    async def _apply_agent_status(
        self,
        wait: WorkflowRunWaitEntity,
        conversation_id: str,
        agent_status: dict[str, object],
        *,
        reconciled: bool = False,
    ) -> bool:
        status = agent_status.get("status")
        if status in {"RUNNING", "WAITING"}:
            return False
        if status == "NOT_FOUND":
            if not reconciled:
                return False
            # The conversation is gone; the run can never resume on its own.
            await self._engine.fail_internal(
                _AGENT, conversation_id, error="Agent conversation no longer exists"
            )
            return True

        ctx = await self._run_context_for_wait(wait)
        ctx_token = set_current_context(ctx)
        try:
            raw_output = agent_status.get("output_data")
            if status == "COMPLETED":
                if reconciled:
                    logger.warning(
                        "workflow.reconcile.resuming_lost_completion",
                        run_id=str(wait.run_id),
                        conversation_id=conversation_id,
                    )
                await self._engine.resume_internal(
                    _AGENT,
                    conversation_id,
                    # Possibly a bare string from an agent without an output
                    # schema; the engine normalizes it into the node output.
                    raw_output or {},  # type: ignore[arg-type]
                    ctx=ctx,
                )
                return True

            output: dict[str, object] = (
                dict(raw_output) if isinstance(raw_output, dict) else {}
            )
            error = str(
                output.get("error")
                or agent_status.get("error")
                or "Agent execution failed"
            )
            output.setdefault("error", error)
            await self._engine.fail_internal(
                _AGENT, conversation_id, error=error, output=output
            )
            return True
        finally:
            reset_current_context(ctx_token)

    async def _run_context_for_wait(self, wait: WorkflowRunWaitEntity) -> Context:
        run = await self._engine.run_repo.get(wait.run_id)
        if run is None:
            raise ValueError(f"Run {wait.run_id} not found for wait {wait.id}")
        return await create_authorization_data_service(self._uow).build_user_context(
            user_id=run.user_id, pod_id=run.pod_id
        )
