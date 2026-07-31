"""Resume/fail workflow runs on agent and function completion, and reconcile
waits whose completion events were lost."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.execution.engine import WorkflowEngine
from app.core.log.log import get_logger

logger = get_logger(__name__)

# A wait this old with no completion event is checked against the source of
# truth by the reconciliation sweep.
RECONCILE_AFTER = timedelta(minutes=10)
RECONCILE_BATCH = 100


class RunResumeService:
    """Handles external completions (agent conversations, function runs)."""

    def __init__(self, engine: WorkflowEngine):
        self._engine = engine
        self._uow = engine.uow

    async def _build_user_context(self, *, user_id: UUID, pod_id: UUID) -> Context:
        return await create_authorization_data_service(self._uow).build_user_context(
            user_id=user_id,
            pod_id=pod_id,
        )

    async def resume_for_agent_conversation(self, *, conversation_id: str) -> bool:
        """Resume or fail the run waiting on an agent conversation."""
        wait = await self._engine.wait_repo.find_active_by_external_ref(
            WorkflowRunWaitType.AGENT, conversation_id
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

    async def resume_for_function_run(
        self,
        *,
        function_run_id: str,
        run_status: str,
        output: dict | None = None,
    ) -> bool:
        """Resume or fail the run waiting on a function run.

        Asymmetry vs. agents: this trusts the completion event's ``output_data``
        when provided (the function run row is the source of truth and the event
        carries the output verbatim), falling back to the adapter only when the
        event omitted it. ``resume_for_agent_conversation`` instead always
        re-reads the conversation status, because an agent's output is nested in
        the event's ``data`` and the conversation row is the canonical record.
        """
        wait = await self._engine.wait_repo.find_active_by_external_ref(
            WorkflowRunWaitType.FUNCTION, function_run_id
        )
        if wait is None:
            logger.debug(
                "workflow.run_resume_service.no_active_workflow_wait_function.observed",
                function_run_id=function_run_id,
            )
            return False

        if output is None:
            status = await self._engine.function_adapter.get_run_status(
                UUID(function_run_id)
            )
            if status["status"] == "COMPLETED" and run_status == "COMPLETED":
                output = status.get("output_data")
            elif status["status"] == "FAILED" and run_status == "FAILED":
                output = {"error": status.get("error")}

        ctx = await self._run_context_for_wait(wait)
        ctx_token = set_current_context(ctx)
        try:
            if run_status == "COMPLETED":
                await self._engine.resume_internal(
                    WorkflowRunWaitType.FUNCTION,
                    function_run_id,
                    output or {},
                    ctx=ctx,
                )
            else:
                await self._engine.fail_internal(
                    WorkflowRunWaitType.FUNCTION,
                    function_run_id,
                    error=(output or {}).get("error") or "Function run failed",
                    output=output,
                )
            return True
        finally:
            reset_current_context(ctx_token)

    async def reconcile_stale_waits(self) -> int:
        """Self-heal runs whose completion/wake events were lost.

        For ACTIVE AGENT/FUNCTION waits older than RECONCILE_AFTER, ask the
        source of truth: finished work resumes the run, failed/vanished work
        fails it, in-progress work is left alone. TIME waits are also swept —
        there is no external source to poll (a timer just needs to fire), so a
        past-due TIME wait whose scheduler wake was lost is fired here. Returns
        the number of waits acted on.
        """
        cutoff = datetime.now(timezone.utc) - RECONCILE_AFTER
        waits = await self._engine.wait_repo.list_active_older_than(
            wait_types=[
                WorkflowRunWaitType.AGENT,
                WorkflowRunWaitType.FUNCTION,
                WorkflowRunWaitType.TIME,
            ],
            created_before=cutoff,
            limit=RECONCILE_BATCH,
        )
        acted = 0
        for wait in waits:
            if not wait.external_ref:
                continue
            try:
                if wait.wait_type == WorkflowRunWaitType.AGENT:
                    status = await self._engine.agent_adapter.get_conversation_status(
                        UUID(wait.external_ref)
                    )
                    handled = await self._apply_agent_status(
                        wait, wait.external_ref, status, reconciled=True
                    )
                elif wait.wait_type == WorkflowRunWaitType.TIME:
                    handled = await self._fire_time_wait_if_due(wait)
                else:
                    status = await self._engine.function_adapter.get_run_status(
                        UUID(wait.external_ref)
                    )
                    handled = await self._apply_function_status(wait, status)
                if handled:
                    acted += 1
            except Exception:
                logger.error(
                    "workflow.reconcile.failed",
                    wait_id=str(wait.id),
                    run_id=str(wait.run_id),
                    exc_info=True,
                )
        if acted:
            logger.debug("workflow.reconcile.recovered", count=acted)
        return acted

    # -- internals ---------------------------------------------------------------

    async def _apply_agent_status(
        self,
        wait: WorkflowRunWaitEntity,
        conversation_id: str,
        agent_status: dict,
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
                WorkflowRunWaitType.AGENT,
                conversation_id,
                error="Agent conversation no longer exists",
            )
            return True

        ctx = await self._run_context_for_wait(wait)
        ctx_token = set_current_context(ctx)
        try:
            output = agent_status.get("output_data")
            if status == "COMPLETED":
                if reconciled:
                    logger.warning(
                        "workflow.reconcile.resuming_lost_completion",
                        run_id=str(wait.run_id),
                        conversation_id=conversation_id,
                    )
                await self._engine.resume_internal(
                    WorkflowRunWaitType.AGENT,
                    conversation_id,
                    output or {},
                    ctx=ctx,
                )
                return True

            if output is None:
                output = {}
            error = (
                output.get("error")
                or agent_status.get("error")
                or "Agent execution failed"
            )
            output.setdefault("error", error)
            await self._engine.fail_internal(
                WorkflowRunWaitType.AGENT,
                conversation_id,
                error=error,
                output=output,
            )
            return True
        finally:
            reset_current_context(ctx_token)

    async def _fire_time_wait_if_due(self, wait: WorkflowRunWaitEntity) -> bool:
        """Fire a TIME wait whose scheduler wake was lost, once it is past due.

        Unlike AGENT/FUNCTION waits there is no external system to poll — a timer
        just needs to elapse. We only fire once ``scheduled_at`` has passed so a
        wait legitimately scheduled far in the future is left alone; resume is a
        no-op if the wait already completed (a duplicate with the primary wake).
        """
        scheduled_at_raw = (wait.payload or {}).get("scheduled_at")
        if not scheduled_at_raw:
            return False
        try:
            scheduled_at = datetime.fromisoformat(scheduled_at_raw)
        except TypeError, ValueError:
            logger.warning(
                "workflow.reconcile.time_wait_bad_scheduled_at", wait_id=str(wait.id)
            )
            return False
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        if scheduled_at > datetime.now(timezone.utc):
            return False  # not due yet

        logger.warning(
            "workflow.reconcile.firing_lost_timer",
            run_id=str(wait.run_id),
            wait_id=str(wait.id),
        )
        ctx = await self._run_context_for_wait(wait)
        ctx_token = set_current_context(ctx)
        try:
            await self._engine.resume_internal(
                WorkflowRunWaitType.TIME,
                wait.external_ref,
                {"payload": {}, "metadata": {}, "llm_output": {}},
                ctx=ctx,
            )
        finally:
            reset_current_context(ctx_token)
        return True

    async def _apply_function_status(
        self, wait: WorkflowRunWaitEntity, status: dict
    ) -> bool:
        if status["status"] == "COMPLETED":
            logger.warning(
                "workflow.reconcile.resuming_lost_completion",
                run_id=str(wait.run_id),
                function_run_id=wait.external_ref,
            )
            ctx = await self._run_context_for_wait(wait)
            ctx_token = set_current_context(ctx)
            try:
                await self._engine.resume_internal(
                    WorkflowRunWaitType.FUNCTION,
                    wait.external_ref,
                    status.get("output_data") or {},
                    ctx=ctx,
                )
            finally:
                reset_current_context(ctx_token)
            return True
        if status["status"] in {"FAILED", "NOT_FOUND"}:
            await self._engine.fail_internal(
                WorkflowRunWaitType.FUNCTION,
                wait.external_ref,
                error=status.get("error") or "Function run no longer exists",
            )
            return True
        return False

    async def _run_context_for_wait(self, wait: WorkflowRunWaitEntity) -> Context:
        run = await self._engine.run_repo.get(wait.run_id)
        if run is None:
            raise ValueError(f"Run {wait.run_id} not found for wait {wait.id}")
        return await self._build_user_context(user_id=run.user_id, pod_id=run.pod_id)
