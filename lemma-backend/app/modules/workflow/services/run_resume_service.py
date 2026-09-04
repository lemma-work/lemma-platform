"""Resume/fail workflow runs on agent and function completion, and reconcile
waits whose completion events were lost."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import settings
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

# Agent wait reasons that resolve themselves. A whitelist on purpose: a wait
# reason added later is subject to the ceiling until someone decides otherwise,
# because the failure mode of guessing wrong in the other direction is a ceiling
# that silently stops applying.
SELF_RESOLVING_WAIT_REASONS = frozenset({"SNOOZE"})

# Waits blocked on a person get their own, far larger ceiling. The machine
# ceiling exists to catch a hang; a human ceiling can only ever catch a person
# not answering, which is not a fault and is common — a workflow that asks a
# question at 18:00 and kills itself at midnight is a worse outcome than one
# that reads as still waiting. This bounds the truly abandoned without punishing
# ordinary out-of-hours delay.
HUMAN_WAIT_CEILING_MULTIPLIER = 12


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
        past-due TIME wait whose scheduler wake was lost is fired here.

        HUMAN waits are swept for the ceiling alone. Nothing can be polled: a
        form is resolved by somebody answering it. But a form assigned to
        someone who has left, or whose pod membership was removed
        (`assigned_pod_member_id` is ON DELETE SET NULL), otherwise leaves its
        run WAITING forever — and the inbox notification expires at 72h, so the
        only visible trace of it disappears while the run stays open. The human
        ceiling is deliberately far longer than the machine one; see
        `_expire_overdue_wait`.

        Returns the number of waits acted on.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - RECONCILE_AFTER
        machine_cutoff = now - timedelta(seconds=settings.workflow_wait_max_age_seconds)
        waits = await self._engine.wait_repo.list_active_older_than(
            wait_types=[
                WorkflowRunWaitType.AGENT,
                WorkflowRunWaitType.FUNCTION,
                WorkflowRunWaitType.TIME,
                WorkflowRunWaitType.HUMAN,
            ],
            created_before=cutoff,
            limit=RECONCILE_BATCH,
        )
        acted = 0
        for wait in waits:
            # A HUMAN wait is the one kind with no external ref: a form is
            # answered through the wait row, not by an outside system.
            if not wait.external_ref and wait.wait_type != WorkflowRunWaitType.HUMAN:
                continue
            try:
                # Fetched once and reused: the expiry check needs it to tell a
                # self-waking agent (healthy) from a hung one, and
                # _apply_agent_status needs the same answer immediately after.
                agent_status = (
                    await self._engine.agent_adapter.get_conversation_status(
                        UUID(wait.external_ref)
                    )
                    if wait.wait_type == WorkflowRunWaitType.AGENT
                    else None
                )
                # A wait past the ceiling is over regardless of what the source
                # of truth says. Without this an agent that hangs (rather than
                # fails) keeps its run non-terminal forever, and the run reads
                # as "still going" for the rest of time.
                if await self._expire_overdue_wait(
                    wait, machine_cutoff, now=now, agent_status=agent_status
                ):
                    acted += 1
                    continue
                if wait.wait_type == WorkflowRunWaitType.HUMAN:
                    # Past the ceiling it is already handled above; short of it
                    # there is nothing to reconcile against, so leave it.
                    continue
                if wait.wait_type == WorkflowRunWaitType.AGENT:
                    handled = await self._apply_agent_status(
                        wait, wait.external_ref, agent_status or {}, reconciled=True
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

    async def _expire_overdue_wait(
        self,
        wait: WorkflowRunWaitEntity,
        machine_cutoff: datetime,
        *,
        now: datetime,
        agent_status: dict | None = None,
    ) -> bool:
        """Fail the run when its wait has outlived the configured ceiling.

        TIME waits are exempt: a wait-until node is *supposed* to sit for as
        long as it was told to, and `_fire_time_wait_if_due` already handles a
        lost timer.

        An AGENT wait is exempt only for `wait_reason` values on
        `SELF_RESOLVING_WAIT_REASONS` — deliberately a whitelist, not "anything
        that is not HUMAN". A new wait reason is subject to the ceiling until
        someone decides it wakes itself; defaulting the other way would let a
        reason nobody thought about silently disable the ceiling.

        `SNOOZE` is on it: a sleeping agent wakes itself and is healthy, so
        failing its run would be a silent wrong outcome rather than a visible
        error. A wait blocked on a *person* is not exempt at this ceiling, but it
        gets a much longer one.
        """
        if wait.wait_type == WorkflowRunWaitType.TIME:
            return False
        wait_reason = (agent_status or {}).get("wait_reason")
        if wait_reason in SELF_RESOLVING_WAIT_REASONS:
            return False
        created_at = wait.created_at
        if created_at is None:
            return False
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        # A form wait is blocked on a person by construction; an agent wait is
        # only when its own status says so.
        human_paced = (
            wait.wait_type == WorkflowRunWaitType.HUMAN or wait_reason == "HUMAN"
        )
        seconds = settings.workflow_wait_max_age_seconds
        if human_paced:
            seconds *= HUMAN_WAIT_CEILING_MULTIPLIER
        cutoff = now - timedelta(seconds=seconds) if human_paced else machine_cutoff
        if created_at > cutoff:
            return False

        hours = seconds / 3600
        logger.warning(
            "workflow.reconcile.wait_expired",
            run_id=str(wait.run_id),
            wait_id=str(wait.id),
            wait_type=wait.wait_type.value,
        )
        # Stop the work before failing the run. `cancel_run` already does this;
        # without it here a hung agent or function keeps burning a sandbox after
        # the run that was waiting on it is already marked failed.
        await self._engine.stop_underlying_work(wait)
        # A form says why in the terms of the thing that did not happen: nobody
        # answered. "Human step did not finish" reads as a system fault, and
        # this one is not.
        error = (
            f"Nobody answered this form within {hours:g}h, so the run was stopped."
            if wait.wait_type == WorkflowRunWaitType.HUMAN
            else (
                f"{wait.wait_type.value.title()} step did not finish within "
                f"{hours:g}h and was stopped."
            )
        )
        await self._engine.fail_for_wait(wait, error=error)
        return True

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
        self, wait: WorkflowRunWaitEntity, status: dict[str, object]
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
