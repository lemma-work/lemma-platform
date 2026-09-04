"""Start or wake workflow runs when schedules fire."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import is_pod_default_agent
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.modules.workflow.domain.context import TriggerContext
from app.modules.workflow.domain.start import WorkflowStartType
from app.modules.workflow.domain.wait import WorkflowRunWaitType
from app.modules.workflow.execution.engine import WorkflowEngine
from app.core.log.log import get_logger
from app.modules.schedule.contracts.dispatch import (
    claim_schedule_run,
    mark_run_dispatched,
    mark_run_failed,
    record_fire,
    get_schedule,
    record_dispatch_dead_letter,
    record_pre_dispatch_failure,
)
from app.modules.schedule.contracts import (
    ScheduleFireStatus,
    ScheduleRunStatus,
    ScheduleType,
)

logger = get_logger(__name__)


def _schedule_pod_id(schedule) -> UUID:
    pod_id = schedule.pod_id
    if pod_id is None:
        raise ValueError("Target schedule has no pod_id")
    return pod_id


def _schedule_run_user_id(schedule, user_id: UUID | str | None) -> UUID:
    if user_id is not None:
        return UUID(str(user_id))
    if schedule.schedule_type == ScheduleType.DATASTORE:
        raise ValueError(f"Datastore schedule {schedule.id} fired without a row owner")
    return schedule.user_id


def _schedule_run_identity(schedule_run) -> tuple[UUID, str]:
    if schedule_run.user_id is None or schedule_run.target_run_id is None:
        raise RuntimeError(f"Schedule run {schedule_run.id} has no execution identity")
    return schedule_run.user_id, schedule_run.target_run_id


def _accept_inactive_one_time_fire(
    schedule, *, schedule_event_id: str, source_occurred_at: datetime | None
) -> bool:
    if schedule.schedule_type != ScheduleType.TIME or source_occurred_at is None:
        return False
    scheduled_at = schedule.config.get("scheduled_at")
    if not scheduled_at or schedule.config.get("cron"):
        return False
    try:
        configured = datetime.fromisoformat(str(scheduled_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if configured.tzinfo is None:
        configured = configured.replace(tzinfo=timezone.utc)
    else:
        configured = configured.astimezone(timezone.utc)
    if source_occurred_at.tzinfo is None:
        occurred_at = source_occurred_at.replace(tzinfo=timezone.utc)
    else:
        occurred_at = source_occurred_at.astimezone(timezone.utc)
    expected_event_id = f"cron:{schedule.id}:{occurred_at.isoformat()}"
    return configured == occurred_at and schedule_event_id == expected_event_id


def _agent_target_ref(schedule, *, pod_id: UUID) -> ResourceRef:
    """What ``agent.execute`` is asked about for this target.

    The pod's own assistant is asked about pod-scoped. Its row's id *is* the
    pod's, so both arms name the same thing -- but the resource type is not
    cosmetic: grants match on (type, id), and an AGENT-typed check would newly
    hit the resource-owner shortcut for whoever created the pod.
    """
    if is_pod_default_agent(schedule.agent_id, pod_id=pod_id):
        return ResourceRef.pod(pod_id)
    return ResourceRef(
        resource_type=ResourceType.AGENT,
        resource_id=schedule.agent_id,
        pod_id=pod_id,
    )


#: What a firing carries alongside its payload: the source's routing key, its
#: `source_event_id`, and whatever else the source chose to say about the
#: delivery. Free-form because each source decides what belongs there.
FiringMetadata = dict[str, Any]


def _conversation_metadata(
    schedule, metadata: FiringMetadata | None
) -> FiringMetadata | None:
    """What the started conversation should know about where it is.

    A webhook source may say which repository its delivery came from -- see
    `NormalizedWebhook.context` -- and that turns a triggered run from "an agent
    told about a pull request" into "an agent standing in the checkout with the
    branch already out". `parse_project_repo` validates it downstream, so
    nothing here has to trust the shape.

    The schedule's account becomes the clone identity. Deliberately the *user's*
    account and not the App installation: the sandbox's `git` and `gh` act as
    the person, so the work an agent pushes is attributed to whoever owns the
    repository rather than to a bot nobody recognises.
    """
    repo = (metadata or {}).get("repo")
    if not isinstance(repo, dict) or not repo:
        return None
    bound = dict(repo)
    if schedule.account_id is not None:
        bound["account_id"] = str(schedule.account_id)
    return {"repo": bound}


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

    async def _start_agent_for_schedule(
        self,
        schedule,
        *,
        pod_id: UUID,
        user_id: UUID,
        trigger: TriggerContext,
        target_run_id: str,
        metadata: FiringMetadata | None = None,
    ) -> UUID:
        """Start the conversation this firing hands over to.

        One arm, because the pod's own assistant is looked up by id like every
        other agent now. The schedule's instruction rides along either way: an
        agent's standing instruction says what it is for, and this says what
        *this run* is for.
        """
        return await self._engine.agent_adapter.run_agent_by_id(
            agent_id=schedule.agent_id,
            input_data=trigger.to_context_value(),
            pod_id=pod_id,
            user_id=user_id,
            conversation_id=UUID(target_run_id),
            source="SCHEDULE",
            instructions=schedule.instruction,
            conversation_metadata=_conversation_metadata(schedule, metadata),
        )

    async def handle_schedule_fired(
        self,
        *,
        schedule_id: str,
        user_id: UUID | str | None,
        payload: dict,
        schedule_event_id: str,
        metadata: dict | None = None,
        llm_output: dict | None = None,
        source_occurred_at: datetime | None = None,
    ) -> None:
        # 1. A wake for one already-suspended execution, rather than a schedule.
        if await self._dispatch_wake(
            payload=payload,
            user_id=user_id,
            metadata=metadata,
            llm_output=llm_output,
        ):
            return

        # 2. A schedule targeting a workflow or agent.
        schedule = await get_schedule(self._uow, UUID(schedule_id))
        if schedule is None:
            logger.debug(
                "workflow.schedule_start_service.no_target_schedule.observed",
                schedule_id=schedule_id,
            )
            return
        if not schedule.has_target:
            # An internal schedule is a wait timer and never had a target; if
            # `_dispatch_wake` did not claim it there is nothing to record.
            # A schedule a person made always had one -- creation refuses
            # without it -- so a missing target means the workflow or agent was
            # deleted out from under it. PS-SCHED-030: record the firing as
            # failed and say the target is missing, rather than dropping it
            # into a debug log nobody reads. The ledger row is what the failure
            # breaker counts, so a schedule left pointing at nothing is
            # eventually paused and its owner emailed.
            if not schedule.is_internal:
                await record_pre_dispatch_failure(
                    self._uow,
                    schedule,
                    source_event_id=schedule_event_id,
                    error_type="ScheduleTargetMissing",
                )
                await self._record_fire(
                    schedule,
                    status=ScheduleFireStatus.ERROR,
                    error=(
                        "The workflow or agent this schedule starts no longer "
                        "exists. Point the schedule at another target, or "
                        "delete it."
                    ),
                )
            logger.debug(
                "workflow.schedule_start_service.no_target_schedule.observed",
                schedule_id=schedule_id,
            )
            return
        if not schedule.is_active and not _accept_inactive_one_time_fire(
            schedule,
            schedule_event_id=schedule_event_id,
            source_occurred_at=source_occurred_at,
        ):
            return
        run_user_id = _schedule_run_user_id(schedule, user_id)

        schedule_run = await claim_schedule_run(
            self._uow,
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
            return
        execution_user_id, target_run_id = _schedule_run_identity(schedule_run)
        if schedule_run.status == ScheduleRunStatus.DEAD_LETTERED:
            await self._record_fire(
                schedule,
                run_id=target_run_id,
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
                    user_id=execution_user_id,
                    trigger=trigger,
                    schedule_event_id=schedule_event_id,
                    target_run_id=target_run_id,
                )
                await mark_run_dispatched(self._uow, schedule_run.id)
                await self._record_fire(
                    schedule,
                    run_id=run_id,
                )
            except Exception as exc:
                run_status = await mark_run_failed(self._uow, schedule_run.id, exc)
                await self._record_fire(
                    schedule,
                    run_id=target_run_id,
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
                    user_id=execution_user_id,
                    pod_id=pod_id,
                )
                await ctx.require(
                    Permissions.AGENT_EXECUTE,
                    _agent_target_ref(schedule, pod_id=pod_id),
                )
                ctx_token = set_current_context(ctx)
                try:
                    conversation_id = await self._start_agent_for_schedule(
                        schedule,
                        pod_id=pod_id,
                        user_id=execution_user_id,
                        trigger=trigger,
                        target_run_id=target_run_id,
                        metadata=metadata,
                    )
                finally:
                    reset_current_context(ctx_token)
                await mark_run_dispatched(self._uow, schedule_run.id)
                await self._record_fire(
                    schedule,
                    run_id=str(conversation_id),
                )
            except Exception as exc:
                run_status = await mark_run_failed(self._uow, schedule_run.id, exc)
                logger.debug(
                    "workflow.schedule_start_service.start_agent_schedule.propagated",
                    agent_id=str(schedule.agent_id),
                    schedule_id=schedule_id,
                    exc_info=True,
                )
                await self._record_fire(
                    schedule,
                    run_id=target_run_id,
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

    async def _dispatch_wake(
        self,
        *,
        payload: dict,
        user_id: UUID | str | None,
        metadata: dict | None,
        llm_output: dict | None,
    ) -> bool:
        """Route a timer that resumes something already waiting. True if handled.

        Both wakes have the same shape — the timer carries the ``wait_ref`` that
        resolves to exactly one ACTIVE wait — and differ only in what they resume:
        a snoozed conversation, or a suspended workflow run.
        """
        if payload.get("conversation_id"):
            await self._wake_snoozed_conversation(external_ref=payload.get("wait_ref"))
            return True
        if payload.get("workflow_run_id"):
            await self._handle_timer_fire(
                payload=payload,
                user_id=user_id,
                metadata=metadata,
                llm_output=llm_output,
            )
            return True
        return False

    async def _wake_snoozed_conversation(self, *, external_ref: str | None) -> None:
        """Resume the agent whose snooze timer just fired."""
        if not external_ref:
            logger.debug("workflow.schedule_start_service.snooze_wake_no_ref.observed")
            return
        from app.modules.agent.domain.wait import AgentWaitWakeReason
        from app.modules.agent.infrastructure.wait_repository import (
            AgentConversationWaitRepository,
        )
        from app.modules.agent.services.snooze_wake_service import SnoozeWakeService

        wait = await AgentConversationWaitRepository(
            self._uow
        ).find_active_by_external_ref(external_ref)
        if wait is None:
            # Already woken by the reconciliation sweep, cancelled, or long gone.
            # Duplicate and stale timer fires are no-ops by construction.
            logger.debug("workflow.schedule_start_service.snooze_wake_stale.observed")
            return
        await SnoozeWakeService(self._uow).wake(
            wait=wait, reason=AgentWaitWakeReason.TIMER
        )

    async def _handle_timer_fire(
        self,
        *,
        payload: dict,
        user_id: UUID | str | None,
        metadata: dict | None,
        llm_output: dict | None,
    ) -> None:
        """Resume the one wait this timer was created for.

        The per-wait token is required so sequential timers on the same run
        cannot cross-resume each other.
        """
        wait_ref = payload.get("wait_ref")
        if not wait_ref:
            raise ValueError("wait_ref is required for workflow timer fires")
        await self._wake_run(
            run_id=str(payload["workflow_run_id"]),
            external_ref=str(wait_ref),
            user_id=UUID(str(user_id)) if user_id else None,
            payload=payload,
            metadata=metadata,
            llm_output=llm_output,
        )

    async def _wake_run(
        self,
        *,
        run_id: str,
        external_ref: str,
        user_id: UUID | None,
        payload: dict,
        metadata: dict | None,
        llm_output: dict | None,
    ) -> None:
        logger.debug(
            "workflow.schedule_start_service.waking_workflow_run_scheduler.observed",
            run_id=run_id,
        )
        run = await self._engine.run_repo.get(UUID(run_id))
        if run is None:
            return
        # The run row owns the wake. A timer that names an owner must agree with
        # it (a mismatched timer must never resume someone else's run); a timer
        # persisted before ownership existed names nobody and simply adopts it.
        if user_id is not None and run.user_id != user_id:
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
        schedule,
        *,
        run_id: str | None = None,
        status=None,
        error: str | None = None,
        dispatch_dead_lettered: bool = False,
    ) -> None:
        resolved = status or ScheduleFireStatus.TRIGGERED
        await record_fire(
            self._uow,
            schedule.id,
            status=resolved,
            run_id=run_id,
            error=error,
        )
        if dispatch_dead_lettered:
            await record_dispatch_dead_letter(self._uow, schedule)
        await self._uow.commit()
