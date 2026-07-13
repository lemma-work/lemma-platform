"""Start or wake workflow runs when schedules fire."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.modules.workflow.domain.context import TriggerContext
from app.modules.workflow.domain.start import WorkflowStartType
from app.modules.workflow.domain.wait import WorkflowRunWaitType
from app.modules.workflow.execution.engine import WorkflowEngine
from app.core.log.log import get_logger
from app.composition.workflow_schedule_runtime import (
    ScheduleRepository,
    ScheduleRunOutcomeService,
    ScheduleRunRepository,
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
        user_id: UUID | str,
        payload: dict,
        schedule_event_id: str,
        metadata: dict | None = None,
        llm_output: dict | None = None,
        source_occurred_at: datetime | None = None,
    ) -> None:
        # 1. A wake for a specific run. The per-wait token is required so
        # sequential timers cannot cross-resume.
        workflow_run_id = payload.get("workflow_run_id")
        if workflow_run_id:
            wait_ref = payload.get("wait_ref")
            if not wait_ref:
                raise ValueError("wait_ref is required for workflow timer fires")
            await self._wake_run(
                run_id=str(workflow_run_id),
                external_ref=str(wait_ref),
                user_id=UUID(str(user_id)),
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
        run_user_id = UUID(str(user_id))

        run_repo = ScheduleRunRepository(self._uow)
        schedule_run = await run_repo.claim(
            schedule_id=schedule.id,
            user_id=run_user_id,
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
        if schedule_run.status == ScheduleRunStatus.DEAD_LETTERED:
            await self._record_fire(
                schedule_repo,
                schedule,
                run_id=schedule_run.target_run_id,
                status=ScheduleFireStatus.ERROR,
                error="Target dispatch exhausted automatic retries",
                dispatch_dead_lettered=True,
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
                    user_id=schedule_run.user_id,
                    trigger=trigger,
                    schedule_event_id=schedule_event_id,
                    target_run_id=schedule_run.target_run_id,
                )
                await run_repo.mark_dispatched(schedule_run.id)
                await self._record_fire(
                    schedule_repo,
                    schedule,
                    run_id=run_id,
                )
            except Exception as exc:
                run_status = await run_repo.mark_failed(schedule_run.id, exc)
                await self._record_fire(
                    schedule_repo,
                    schedule,
                    run_id=schedule_run.target_run_id,
                    status=ScheduleFireStatus.ERROR,
                    error=f"{type(exc).__name__}: target dispatch failed",
                    dispatch_dead_lettered=(
                        run_status == ScheduleRunStatus.DEAD_LETTERED
                    ),
                )
                raise
            return

        if schedule.agent_id is not None:
            try:
                pod_id = _schedule_pod_id(schedule)
                ctx = await self._build_user_context(
                    user_id=schedule_run.user_id,
                    pod_id=pod_id,
                )
                await ctx.require(
                    Permissions.AGENT_EXECUTE,
                    ResourceRef(
                        resource_type=ResourceType.AGENT,
                        resource_id=schedule.agent_id,
                        pod_id=pod_id,
                    ),
                )
                ctx_token = set_current_context(ctx)
                try:
                    conversation_id = await self._engine.agent_adapter.run_agent_by_id(
                        agent_id=schedule.agent_id,
                        input_data=trigger.to_context_value(),
                        pod_id=pod_id,
                        user_id=schedule_run.user_id,
                        conversation_id=UUID(schedule_run.target_run_id),
                        source="SCHEDULE",
                    )
                finally:
                    reset_current_context(ctx_token)
                await run_repo.mark_dispatched(schedule_run.id)
                await self._record_fire(
                    schedule_repo,
                    schedule,
                    run_id=str(conversation_id),
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
                    schedule,
                    run_id=schedule_run.target_run_id,
                    status=ScheduleFireStatus.ERROR,
                    error=f"{type(exc).__name__}: target dispatch failed",
                    dispatch_dead_lettered=(
                        run_status == ScheduleRunStatus.DEAD_LETTERED
                    ),
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
        external_ref: str,
        user_id: UUID,
        payload: dict,
        metadata: dict | None,
        llm_output: dict | None,
    ) -> None:
        logger.info("Waking workflow run from scheduler", run_id=run_id, wait_ref=external_ref)
        run = await self._engine.run_repo.get(UUID(run_id))
        if run is None:
            logger.info("No workflow run found for scheduler wake", run_id=run_id)
            return
        if run.user_id != user_id:
            raise ValueError("workflow timer user_id does not match the run owner")
        ctx = await self._build_user_context(user_id=run.user_id, pod_id=run.pod_id)
        ctx_token = set_current_context(ctx)
        try:
            await self._engine.resume_internal(
                WorkflowRunWaitType.TIME,
                external_ref=external_ref,
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
        user_id: UUID,
        trigger: TriggerContext,
        schedule_event_id: str,
        target_run_id: str,
    ) -> str:
        workflow_schedule_event_id = f"{schedule.id}:{schedule_event_id}"
        ctx = await self._build_user_context(
            user_id=user_id,
            pod_id=_schedule_pod_id(schedule),
        )
        ctx_token = set_current_context(ctx)
        try:
            run = await self._engine.start_run(
                run_id=UUID(target_run_id),
                flow_id=schedule.workflow_id,
                user_id=user_id,
                trigger=trigger,
                schedule_event_id=workflow_schedule_event_id,
                ctx=ctx,
            )
            return str(run.id)
        finally:
            reset_current_context(ctx_token)

    async def _record_fire(
        self,
        schedule_repo,
        schedule,
        *,
        run_id: str | None = None,
        status=None,
        error: str | None = None,
        dispatch_dead_lettered: bool = False,
    ) -> None:
        resolved = status or ScheduleFireStatus.TRIGGERED
        await schedule_repo.record_fire(
            schedule.id,
            status=resolved,
            run_id=run_id,
            error=error,
        )
        if dispatch_dead_lettered:
            await ScheduleRunOutcomeService(
                self._uow
            ).record_dispatch_dead_letter(schedule)
        await self._uow.commit()
