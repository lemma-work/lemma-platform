"""Resuming and expiring workflow runs parked on an AGENT step.

An AGENT wait is woken like every other wait, by a work item written in the
transaction that ends what it waits on: the agent's finalize emits
``AgentRunFinished`` and ``events/agent_resume_work.py`` resumes the step from
it. Its ceiling is a ``workflow.wait_expire`` item written with the wait
(``execution/wait_work.py``), which comes here to decide: a conversation that
finished resumes the run (a completion that reached nobody across the cutover),
a hung one fails it, a healthy one is checked again later.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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

    async def expire_agent_wait(
        self, wait: WorkflowRunWaitEntity, *, now: datetime
    ) -> datetime | None:
        """Act on an AGENT wait at its ceiling; when to look again, if ever.

        A finished conversation resumes or fails the run, as its completion
        would have. An agent still working past the ceiling is a hang: it is
        stopped and the run fails. A waiting agent wakes itself and is exempt,
        and one blocked on a person gets the human ceiling.
        """
        if not wait.external_ref:
            return None
        agent_status = await self._engine.agent_adapter.get_conversation_status(
            UUID(wait.external_ref)
        )
        if await self._apply_agent_status(
            wait, wait.external_ref, agent_status, reconciled=True
        ):
            return None
        wait_reason = agent_status.get("wait_reason")
        seconds = ceiling_seconds(wait, human_paced=wait_reason == "HUMAN")
        if seconds is None:
            return None
        if wait_reason in SELF_RESOLVING_WAIT_REASONS:
            return now + timedelta(seconds=seconds)
        created_at = wait.created_at or now
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        due = created_at + timedelta(seconds=seconds)
        if now < due:
            return due
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
        return None

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
