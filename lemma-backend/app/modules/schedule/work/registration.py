"""Registers the schedule kinds. Imported by the worker and by anything that enqueues one.

Handler modules import only ``kinds`` and the runtime at their top, so this
module is cheap to import and never cycles back into the services that import
it.
"""

from __future__ import annotations

from datetime import timedelta

from app.modules.workflow.contracts.run_work import (
    WorkflowRunFinished,
    WorkflowStartRefused,
)
from app.core.infrastructure.work import (
    Exponential,
    FactBus,
    RateUse,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts as default_facts,
    registry as default_registry,
)
from app.modules.schedule.work import (
    audit,
    fire,
    filter as filter_work,
    notify,
    outcome,
    start,
)
from app.modules.schedule.work.kinds import (
    SCHEDULE_ARM,
    SCHEDULE_ARM_AUDIT,
    SCHEDULE_FILTER,
    SCHEDULE_FIRE,
    SCHEDULE_OUTCOME,
    SCHEDULE_PAUSED_NOTICE,
    SCHEDULE_START,
    SCHEDULE_WORKFLOW_FINISHED,
    SCHEDULE_WORKFLOW_REFUSED,
    ArmAudit,
    FilterEvent,
    PausedNotice,
    ScheduleRef,
    StartTarget,
    TargetOutcome,
    TimeFire,
)

#: Two filter calls of one schedule at a time; the rest wait their turn.
FILTER_CONCURRENCY = 2

#: Retries for a start that fails on this run, then dead-lettered (and counted
#: by the breaker). The old consumer retried ten times; most failures here are
#: permanent and say so at once.
START_RETRY = RetryPolicy(
    max_attempts=5,
    max_lease_losses=3,
    unavailable_budget=timedelta(hours=6),
    backoff=Exponential(
        base=timedelta(seconds=10), cap=timedelta(minutes=10), jitter=0.2
    ),
)
#: A provider that answers badly is retried a few times; one that is down is
#: waited out for half an hour before the event is dead-lettered as an outage.
FILTER_RETRY = RetryPolicy(
    max_attempts=6,
    max_lease_losses=3,
    unavailable_budget=timedelta(minutes=30),
    backoff=Exponential(
        base=timedelta(seconds=10), cap=timedelta(minutes=10), jitter=0.2
    ),
)


def register_schedule_work(
    registry: WorkRegistry, facts: FactBus | None = None
) -> None:
    registry.handler(
        kind=SCHEDULE_FIRE,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=TimeFire,
        on_dead=fire.on_fire_dead,
    )(fire.run_time_fire)
    registry.handler(
        kind=SCHEDULE_ARM, cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=ScheduleRef
    )(fire.run_arm)
    registry.handler(
        kind=SCHEDULE_ARM_AUDIT,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=ArmAudit,
    )(audit.run_arm_audit)
    registry.periodic(SCHEDULE_ARM_AUDIT, audit.ARM_AUDIT_EVERY, SCHEDULE_ARM_AUDIT)
    registry.handler(
        kind=SCHEDULE_OUTCOME,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=TargetOutcome,
    )(outcome.run_target_outcome)
    registry.handler(
        kind=SCHEDULE_START,
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=StartTarget,
        retry=START_RETRY,
        on_dead=start.on_start_dead,
        rates=(RateUse("trigger_fire", per_key=True),),
    )(start.run_start_target)
    registry.handler(
        kind=SCHEDULE_FILTER,
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=FilterEvent,
        retry=FILTER_RETRY,
        concurrency=FILTER_CONCURRENCY,
        on_dead=filter_work.on_filter_dead,
        rates=(RateUse("llm_filter", per_key=True), RateUse("llm_filter_pod")),
    )(filter_work.run_filter)
    registry.handler(
        kind=SCHEDULE_PAUSED_NOTICE,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=PausedNotice,
    )(notify.run_paused_notice)
    registry.handler(
        kind=SCHEDULE_WORKFLOW_FINISHED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=WorkflowRunFinished,
    )(outcome.run_workflow_finished)
    registry.handler(
        kind=SCHEDULE_WORKFLOW_REFUSED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=WorkflowStartRefused,
    )(outcome.run_workflow_refused)
    if facts is None:
        return
    facts.subscribe(
        WorkflowRunFinished,
        kind=SCHEDULE_WORKFLOW_FINISHED,
        cls=WorkClass.EVENTS,
        when=outcome.started_by_schedule,
        # A retried run reports again under the same id with a new status.
        dedup=lambda fact: f"schedule-outcome:{fact.run_id}:{fact.status}",
    )
    facts.subscribe(
        WorkflowStartRefused,
        kind=SCHEDULE_WORKFLOW_REFUSED,
        cls=WorkClass.EVENTS,
        when=outcome.started_by_schedule,
        dedup=lambda fact: f"schedule-refused:{fact.run_id}",
    )


register_schedule_work(default_registry, default_facts)
