"""Start or wake workflow runs when schedules fire."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.modules.workflow.domain.context import TriggerContext
from app.modules.workflow.domain.errors import WorkflowConflictError
from app.modules.workflow.domain.start import WorkflowStartType
from app.modules.workflow.domain.wait import WorkflowRunWaitType
from app.modules.workflow.execution.engine import WorkflowEngine
from app.core.log.log import get_logger
from app.composition.workflow_schedule_runtime import (
    ScheduleRepository,
    ScheduleRunRepository,
    schedule_settings,
)
from app.modules.schedule.contracts import ScheduleFireStatus, ScheduleRunStatus

logger = get_logger(__name__)


def _schedule_pod_id(schedule) -> UUID:
    pod_id = schedule.pod_id
    if pod_id is None:
        raise ValueError("Target schedule has no pod_id")
    return pod_id


class ScheduleStartService:
    """Handles schedule.fired events for workflows."""

    def __init__(self, engine: WorkflowEngine):
        self._engine = engine
        self._uow = engine.uow

    async def _build_user_context(self, *, user_id: UUID, pod_id: UUID) -> Context:
        return await create_authorization_data_service(self._uow).build_user_context(
            user_id=user_id,
            pod_id=pod_id,
        )

    async def handle_schedule_fired(
        self,
        *,
        schedule_id: str,
        payload: dict,
        metadata: dict | None = None,
        llm_output: dict | None = None,
        schedule_event_id: str | None = None,
        source_occurred_at: datetime | None = None,
    ) -> None:
        # 1. A wake for a specific run (wait_until timers carry the run id, and —
        # for timers scheduled after the per-wait-token change — a wait_ref that
        # resolves to the exact wait so sequential timers can't cross-resume).
        workflow_run_id = payload.get("workflow_run_id") or payload.get("flow_run_id")
        if workflow_run_id:
            await self._wake_run(
                run_id=str(workflow_run_id),
                external_ref=payload.get("wait_ref"),
                payload=payload,
                metadata=metadata,
                llm_output=llm_output,
            )
            return

        # 2. A schedule targeting a workflow or agent.
        schedule_repo = ScheduleRepository(self._uow)
        schedule = await schedule_repo.get(UUID(schedule_id))
        if schedule is None or (
            schedule.workflow_id is None and schedule.agent_id is None
        ):
            logger.info("No target for schedule", schedule_id=schedule_id)
            return
        if not schedule.is_active:
            logger.info("Inactive schedule skipped", schedule_id=str(schedule.id))
            return
        if not schedule_event_id:
            raise ValueError("schedule_event_id is required for durable delivery")

        run_repo = ScheduleRunRepository(self._uow)
        schedule_run = await run_repo.claim(
            schedule_id=schedule.id,
            source_event_id=schedule_event_id,
            target_kind="WORKFLOW" if schedule.workflow_id is not None else "AGENT",
            payload=payload,
            metadata=metadata,
            llm_output=llm_output,
            source_occurred_at=source_occurred_at,
        )
        if schedule_run is None:
            logger.info(
                "Duplicate or terminal schedule run skipped",
                schedule_id=str(schedule.id),
                source_event_id=schedule_event_id,
            )
            return

        trigger = self._build_trigger(
            schedule.schedule_type.value if schedule.schedule_type else None,
            payload=payload,
            metadata=metadata,
            llm_output=llm_output,
        )

        if schedule.workflow_id is not None:
            try:
                run_id = await self._start_workflow_for_schedule(
                    schedule=schedule,
                    trigger=trigger,
                    schedule_event_id=schedule_event_id,
                )
                await run_repo.mark_dispatched(
                    schedule_run.id, target_run_id=run_id
                )
                await self._record_fire(
                    schedule_repo,
                    run_repo,
                    schedule,
                    run_id=run_id,
                    run_status=ScheduleRunStatus.DISPATCHED,
                )
            except Exception as exc:
                run_status = await run_repo.mark_failed(schedule_run.id, exc)
                await self._record_fire(
                    schedule_repo,
                    run_repo,
                    schedule,
                    status=ScheduleFireStatus.ERROR,
                    error=f"{type(exc).__name__}: target dispatch failed",
                    run_status=run_status,
                )
                raise
            return

        if schedule.agent_id is not None:
            try:
                conversation_id = await self._engine.agent_adapter.run_agent_by_id(
                    agent_id=schedule.agent_id,
                    input_data=trigger.to_context_value(),
                    pod_id=_schedule_pod_id(schedule),
                    user_id=schedule.user_id,
                    source="SCHEDULE",
                    conversation_metadata={"schedule_id": str(schedule_id)},
                    origin_type="SCHEDULE_RUN",
                    origin_id=schedule_run.id,
                )
                await run_repo.mark_dispatched(
                    schedule_run.id, target_run_id=str(conversation_id)
                )
                await self._record_fire(
                    schedule_repo,
                    run_repo,
                    schedule,
                    run_id=str(conversation_id),
                    run_status=ScheduleRunStatus.DISPATCHED,
                )
            except Exception as exc:
                run_status = await run_repo.mark_failed(schedule_run.id, exc)
                logger.exception(
                    "Failed to start agent for schedule",
                    agent_id=str(schedule.agent_id),
                    schedule_id=schedule_id,
                )
                await self._record_fire(
                    schedule_repo,
                    run_repo,
                    schedule,
                    status=ScheduleFireStatus.ERROR,
                    error=f"{type(exc).__name__}: target dispatch failed",
                    run_status=run_status,
                )
                raise

    # -- internals ---------------------------------------------------------------

    def _build_trigger(
        self,
        schedule_type: str | None,
        *,
        payload: dict,
        metadata: dict | None,
        llm_output: dict | None,
    ) -> TriggerContext:
        trigger_type = {
            "TIME": WorkflowStartType.SCHEDULED,
            "WEBHOOK": WorkflowStartType.EVENT,
            "DATASTORE": WorkflowStartType.DATASTORE_EVENT,
        }.get(schedule_type or "", WorkflowStartType.SCHEDULED)
        return TriggerContext(
            trigger_type=trigger_type,
            payload=payload or {},
            metadata=metadata or {},
            llm_output=llm_output or {},
        )

    async def _wake_run(
        self,
        *,
        run_id: str,
        external_ref: str | None = None,
        payload: dict,
        metadata: dict | None,
        llm_output: dict | None,
    ) -> None:
        # Resume the specific wait keyed by its own token when present; fall back
        # to the run id for timers scheduled before the per-wait-token change (so
        # in-flight waits created by the old code path still wake correctly).
        resume_ref = external_ref or run_id
        logger.info("Waking workflow run from scheduler", run_id=run_id, wait_ref=resume_ref)
        run = await self._engine.run_repo.get(UUID(run_id))
        if run is None:
            logger.info("No workflow run found for scheduler wake", run_id=run_id)
            return
        ctx = await self._build_user_context(user_id=run.user_id, pod_id=run.pod_id)
        ctx_token = set_current_context(ctx)
        try:
            await self._engine.resume_internal(
                WorkflowRunWaitType.TIME,
                external_ref=resume_ref,
                output={
                    "payload": payload,
                    "metadata": metadata or {},
                    "llm_output": llm_output or {},
                },
                ctx=ctx,
            )
        finally:
            reset_current_context(ctx_token)

    async def _start_workflow_for_schedule(
        self,
        *,
        schedule,
        trigger: TriggerContext,
        schedule_event_id: str | None,
    ) -> str | None:
        workflow_schedule_event_id = (
            f"{schedule.id}:{schedule_event_id}" if schedule_event_id else None
        )
        try:
            ctx = await self._build_user_context(
                user_id=schedule.user_id,
                pod_id=_schedule_pod_id(schedule),
            )
            ctx_token = set_current_context(ctx)
            try:
                run = await self._engine.start_run(
                    flow_id=schedule.workflow_id,
                    user_id=schedule.user_id,
                    trigger=trigger,
                    schedule_event_id=workflow_schedule_event_id,
                    ctx=ctx,
                )
                return str(run.id)
            finally:
                reset_current_context(ctx_token)
        except WorkflowConflictError:
            logger.info(
                "Workflow run already exists for schedule event",
                source_event_id=schedule_event_id,
            )
            return None

    async def _record_fire(
        self,
        schedule_repo,
        run_repo,
        schedule,
        *,
        run_id: str | None = None,
        status=None,
        error: str | None = None,
        run_status=None,
    ) -> None:
        resolved = status or ScheduleFireStatus.TRIGGERED
        await schedule_repo.record_fire(
            schedule.id,
            status=resolved,
            run_id=run_id,
            error=error,
        )
        tripped_count = await self._apply_failure_policy(
            schedule_repo, run_repo, schedule, run_status
        )
        if tripped_count is not None:
            from app.modules.schedule.domain.events.schedule import ScheduleDeactivated

            self._uow.collect_events(
                [
                    ScheduleDeactivated(
                        schedule_id=schedule.id,
                        user_id=schedule.user_id,
                        schedule_type=schedule.schedule_type,
                        consecutive_failures=tripped_count,
                    )
                ]
            )
        await self._uow.commit()

    async def _apply_failure_policy(
        self, schedule_repo, run_repo, schedule, run_status
    ) -> int | None:
        """Derive the breaker from distinct terminal schedule runs."""
        if run_status == ScheduleRunStatus.DISPATCHED:
            await schedule_repo.set_consecutive_failures(schedule.id, 0)
            return None
        if run_status != ScheduleRunStatus.DEAD_LETTERED:
            return None

        count = await run_repo.consecutive_terminal_failures(schedule.id)
        await schedule_repo.set_consecutive_failures(schedule.id, count)
        threshold = schedule_settings.schedule_max_consecutive_failures
        if threshold <= 0 or count < threshold:
            return None

        # Trip: stop the schedule from firing (all matcher queries filter is_active).
        await schedule_repo.update(schedule.id, is_active=False)
        logger.warning(
            "Circuit breaker deactivated schedule",
            schedule_id=str(schedule.id),
            consecutive_failures=count,
        )
        return count
