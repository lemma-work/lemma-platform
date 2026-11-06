"""Starting the target of a reserved schedule run (``schedule.start_target``).

Runs inside the job's fenced completion transaction: claiming the reservation,
creating the target's run and marking the schedule run DISPATCHED commit with
the item's deletion, so a crash anywhere before that commit starts nothing and
the item is claimed again, and a commit means it started exactly once.

An agent target is started here, through the agent module's contract. A
workflow target is handed to the workflow module as a ``workflow.start`` item
under the run's reserved id, written in the same transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import UUID

from pydantic import JsonValue

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import is_pod_default_agent
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import PermanentFailure
from app.core.log.log import get_logger
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.repositories.fire_ledger import FireLedger, is_unstarted
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import ScheduleRunOutcomeService
from app.modules.workflow.contracts.run_work import (
    WorkflowStartRequest,
    WorkflowTrigger,
    WorkflowTriggerType,
    enqueue_workflow_start,
)

logger = get_logger(__name__)

TARGET_MISSING = "ScheduleTargetMissing"
#: Why a run whose person has left the pod started nothing.
OWNER_NOT_A_MEMBER = "owner_not_a_member"
#: Why a run for someone other than the schedule's owner -- the owner of the
#: row that fired it -- started nothing: they are not in the pod, or may not
#: run the target. Never handed to the owner instead: that would run the
#: row's text with the owner's access.
RUN_PERSON_NOT_A_MEMBER = "run_person_not_a_member"
RUN_PERSON_CANNOT_RUN = "run_person_cannot_run_target"


async def start_reserved_run(uow: SqlAlchemyUnitOfWork, run_id: UUID) -> None:
    ledger = FireLedger(uow.session)
    run = await ledger.lock(run_id)
    if run is None or not is_unstarted(run):
        return
    model = await uow.session.get(Schedule, run.schedule_id)
    if model is None:
        return
    schedule = model.to_entity()
    if not schedule.has_target:
        await _dead_letter(uow, run, schedule, TARGET_MISSING)
        return
    if not schedule.is_active and not _is_its_one_shot(schedule, run):
        await ledger.settle_skipped(run.id, metadata={"skipped_because": "inactive"})
        return
    owner_ctx = await _run_owner_context(uow, run, schedule)
    skipped_because = await _why_not_startable(owner_ctx, run, schedule)
    if skipped_because is not None:
        # The authorizer would refuse the target's execute anyway, but as a
        # dead letter saying only "DomainError", counted against the breaker
        # and mailed to somebody no longer in the pod. A run with nobody in
        # the pod to run it as runs as nobody.
        await ledger.settle_skipped(
            run.id, metadata={"skipped_because": skipped_because}
        )
        return
    if schedule.workflow_id is not None:
        await _hand_to_workflow(uow, run, schedule)
        return
    try:
        await _start_agent(uow, run, schedule, owner_ctx)
    except DomainError as exc:
        if exc.status_code >= 500:
            raise
        raise PermanentFailure(f"{type(exc).__name__}: {exc.code}") from exc


async def _run_owner_context(
    uow: SqlAlchemyUnitOfWork, run: ScheduleRun, schedule: ScheduleEntity
) -> Context | None:
    """Who this run is for, as of now -- the schedule's owner, the owner of
    the row that fired it, or whoever redrove it -- or ``None`` for a run with
    nobody to ask about."""
    if run.user_id is None or schedule.pod_id is None:
        return None
    return await create_authorization_data_service(uow).build_user_context(
        user_id=run.user_id, pod_id=schedule.pod_id
    )


async def _why_not_startable(
    ctx: Context | None, run: ScheduleRun, schedule: ScheduleEntity
) -> str | None:
    """Why this run starts nothing, or ``None`` when it may start."""
    if ctx is None:
        return None
    for_the_owner = run.user_id == schedule.user_id
    if not ctx.belongs_to_its_pod():
        return OWNER_NOT_A_MEMBER if for_the_owner else RUN_PERSON_NOT_A_MEMBER
    if for_the_owner or schedule.pod_id is None:
        # The owner's execute is asked where the target starts, as before.
        return None
    if schedule.workflow_id is not None:
        permission, target = (
            Permissions.WORKFLOW_EXECUTE,
            ResourceRef(
                resource_type=ResourceType.WORKFLOW,
                resource_id=schedule.workflow_id,
                pod_id=schedule.pod_id,
            ),
        )
    else:
        permission = Permissions.AGENT_EXECUTE
        target = _agent_ref(schedule, pod_id=schedule.pod_id)
    return None if await ctx.can(permission, target) else RUN_PERSON_CANNOT_RUN


async def dead_letter_run(
    uow: SqlAlchemyUnitOfWork, run_id: UUID, *, error_type: str
) -> None:
    """The start item gave up: the run says so and the breaker counts it."""
    run = await FireLedger(uow.session).lock(run_id)
    if run is None:
        return
    model = await uow.session.get(Schedule, run.schedule_id)
    if model is None:
        return
    await _dead_letter(uow, run, model.to_entity(), error_type)


async def _dead_letter(
    uow: SqlAlchemyUnitOfWork,
    run: ScheduleRun,
    schedule: ScheduleEntity,
    error_type: str,
) -> None:
    if await ScheduleRunRepository(uow).dead_letter(run.id, error_type=error_type):
        await ScheduleRunOutcomeService(uow).record_dispatch_dead_letter(schedule)


def _is_its_one_shot(schedule: ScheduleEntity, run: ScheduleRun) -> bool:
    """A one-shot TIME schedule is inactive from the moment it fires; its run still starts."""
    return (
        schedule.schedule_type == ScheduleType.TIME
        and not (schedule.config or {}).get("cron")
        and run.source_event_id.startswith(f"cron:{schedule.id}:")
    )


_WORKFLOW_TRIGGERS: dict[ScheduleType, WorkflowTriggerType] = {
    ScheduleType.TIME: "SCHEDULED",
    ScheduleType.WEBHOOK: "EVENT",
    ScheduleType.DATASTORE: "DATASTORE_EVENT",
}


async def _hand_to_workflow(
    uow: SqlAlchemyUnitOfWork, run: ScheduleRun, schedule: ScheduleEntity
) -> None:
    """Queue the workflow's start under the run's reserved id.

    ``workflow.start`` is idempotent on that id, authorizes the owner when it
    runs, and marks this run dispatched in the transaction that creates the
    workflow run: until then the run stays PROCESSING (in flight, for the
    cap), so a DISPATCHED run's ``target_run_id`` always opens. If it cannot
    start the run at all, ``WorkflowStartRefused`` comes back and
    dead-letters this one.
    """
    pod_id = schedule.pod_id
    if pod_id is None or schedule.workflow_id is None:
        raise PermanentFailure("workflow schedule has no pod")
    started = await FireLedger(uow.session).begin_start(run.id)
    if started is None or started.user_id is None or started.target_run_id is None:
        return
    context = trigger_input(
        schedule,
        started.payload,
        started.metadata,
        started.llm_output,
        started.source_occurred_at,
    )
    await enqueue_workflow_start(
        uow.session,
        pod_id=pod_id,
        request=WorkflowStartRequest(
            flow_id=schedule.workflow_id,
            user_id=started.user_id,
            run_id=UUID(started.target_run_id),
            schedule_event_id=workflow_schedule_event_id(
                schedule.id, started.source_event_id
            ),
            trigger=WorkflowTrigger(
                trigger_type=_WORKFLOW_TRIGGERS.get(
                    schedule.schedule_type, "SCHEDULED"
                ),
                payload=_object(context["payload"]),
                metadata=_object(context["metadata"]),
                llm_output=_object(context["llm_output"]),
            ),
            dispatches_schedule_run_id=run.id,
        ),
    )


def workflow_schedule_event_id(schedule_id: UUID, source_event_id: str) -> str:
    """What a schedule-started workflow run records as its source; unique per flow."""
    return f"{schedule_id}:{source_event_id}"


def _object(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


async def _start_agent(
    uow: SqlAlchemyUnitOfWork,
    run: ScheduleRun,
    schedule: ScheduleEntity,
    owner_ctx: Context | None,
) -> None:
    from app.modules.agent.contracts.automation import (
        build_agent_control_adapter,
    )

    pod_id = schedule.pod_id
    if pod_id is None or schedule.agent_id is None:
        raise PermanentFailure("agent schedule has no pod")
    started = await FireLedger(uow.session).begin_start(run.id)
    if started is None or started.user_id is None or started.target_run_id is None:
        return
    ctx = owner_ctx or await create_authorization_data_service(uow).build_user_context(
        user_id=started.user_id, pod_id=pod_id
    )
    await ctx.require(Permissions.AGENT_EXECUTE, _agent_ref(schedule, pod_id=pod_id))
    token = set_current_context(ctx)
    try:
        await build_agent_control_adapter(uow).run_agent_by_id(
            agent_id=schedule.agent_id,
            input_data=trigger_input(
                schedule,
                started.payload,
                started.metadata,
                started.llm_output,
                started.source_occurred_at,
            ),
            pod_id=pod_id,
            user_id=started.user_id,
            conversation_id=UUID(started.target_run_id),
            source="SCHEDULE",
            instructions=schedule.instruction,
            conversation_metadata=conversation_metadata(schedule, started.metadata),
        )
    finally:
        reset_current_context(token)
    await ScheduleRunRepository(uow).mark_dispatched(run.id)


def _agent_ref(schedule: ScheduleEntity, *, pod_id: UUID) -> ResourceRef:
    """The pod's own assistant is asked about pod-scoped; grants match on (type, id)."""
    if is_pod_default_agent(schedule.agent_id, pod_id=pod_id):
        return ResourceRef.pod(pod_id)
    return ResourceRef(
        resource_type=ResourceType.AGENT, resource_id=schedule.agent_id, pod_id=pod_id
    )


def trigger_input(
    schedule: ScheduleEntity,
    payload: Mapping[str, JsonValue],
    metadata: Mapping[str, JsonValue],
    llm_output: Mapping[str, JsonValue],
    source_occurred_at: datetime | None,
) -> dict[str, JsonValue]:
    """The event as the target reads it: ``payload``, ``metadata``, ``llm_output``.

    The facts about the firing itself go in ``metadata`` -- which schedule,
    when it ran, and which occurrence it was for, which is not the same moment
    once a retry or a busy queue sits between them.
    """
    schedule_type = schedule.schedule_type.value
    fire_metadata: dict[str, JsonValue] = {
        **dict(metadata),
        "schedule_id": str(schedule.id),
        "schedule_name": schedule.name,
        "trigger_type": schedule_type,
        "fired_at": datetime.now(timezone.utc).isoformat(),
    }
    if source_occurred_at is not None:
        fire_metadata["scheduled_for"] = source_occurred_at.isoformat()
    zone = (schedule.config or {}).get("timezone")
    if isinstance(zone, str) and zone:
        fire_metadata["timezone"] = zone
    return {
        "payload": dict(payload),
        "metadata": fire_metadata,
        "llm_output": dict(llm_output),
    }


def conversation_metadata(
    schedule: ScheduleEntity, metadata: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """What the started conversation knows about where it came from.

    That a schedule opened it is the conversation's ``origin``, set by the
    agent module, and how a run tells that nobody is waiting on it, so it does
    not reach for ``ask_user`` at six in the morning. ``schedule_id`` is what
    a list of a pod's runs groups them by, since the name can be edited and
    the title is derived from it. A webhook source's repository context, when
    present, becomes the run's checkout, cloned through the pod's `github`
    connector.
    """
    started: dict[str, JsonValue] = {
        "schedule_id": str(schedule.id),
        "schedule_type": schedule.schedule_type.value,
    }
    if schedule.name:
        started["schedule_name"] = schedule.name
    repo = metadata.get("repo")
    if isinstance(repo, dict) and repo:
        # The repository only. Which account clones it is the pod's `github`
        # connector's decision, for the person the run acts for.
        started["repo"] = dict(repo)
    return started
