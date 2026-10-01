"""Apply target outcomes to the schedule ledger and failure circuit breaker."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.errors import (
    ScheduleFilterUndecidedError,
    ScheduleTriageDeciderMissingError,
    ScheduleTriageUndecidedError,
)
from app.modules.schedule.domain.events.schedule import (
    ScheduleDeactivated,
    ScheduleRunCompleted,
)
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleRunEntity,
    ScheduleRunStatus,
)
from app.modules.schedule.domain.triage import TriageVerdict
from app.modules.schedule.repositories.held_runs import HeldRunRepository
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: The `error_type` of a fire whose filter could not decide. Distinct from the
#: two quota failures, because the fix is different again: read the decision,
#: and make the instruction one the event can answer. A triage's own failures
#: are its subclasses' `error_type`s.
FILTER_UNDECIDED = ScheduleFilterUndecidedError.error_type
_EXPLANATIONS = {
    error.error_type: error.explanation
    for error in (
        ScheduleFilterUndecidedError,
        ScheduleTriageUndecidedError,
        ScheduleTriageDeciderMissingError,
    )
}


def _target_kind(schedule: ScheduleEntity) -> str:
    return "WORKFLOW" if schedule.workflow_id is not None else "AGENT"


def _json(value: Mapping[str, object]) -> dict[str, JsonValue]:
    """`value` as plain JSON, which is all a held event's columns may hold.

    A datastore row can carry a timestamp or a decimal; `default=str` renders
    those as the event's filter and its target always read them.
    """
    decoded = json.loads(json.dumps(dict(value), default=str))
    return decoded if isinstance(decoded, dict) else {}


class ScheduleRunOutcomeService:
    """Own schedule-run terminal transitions and breaker accounting."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.run_repository = ScheduleRunRepository(uow)
        self.schedule_repository = ScheduleRepository(uow=uow)

    async def record_target_outcome(
        self,
        *,
        target_kind: str,
        target_run_id: str,
        status: ScheduleRunStatus,
        completed_at: datetime | None,
        error_type: str | None = None,
    ) -> bool:
        """Record a target outcome once and update its schedule's streak."""
        schedule_run = await self.run_repository.transition_target_outcome(
            target_kind=target_kind,
            target_run_id=target_run_id,
            status=status,
            completed_at=completed_at,
            error_type=error_type,
        )
        if schedule_run is None:
            return False

        schedule = await self.schedule_repository.get_for_update(
            schedule_run.schedule_id
        )
        if schedule is None:
            raise LookupError(
                f"Schedule {schedule_run.schedule_id} disappeared during outcome update"
            )

        # In the same transaction as the outcome it reports, and after the
        # schedule is loaded so the pod is known without a second read.
        self.uow.collect_events(
            [
                ScheduleRunCompleted(
                    schedule_id=schedule.id,
                    schedule_type=schedule.schedule_type,
                    pod_id=schedule.pod_id,
                    status=status.value,
                    # The run's own owner first: for a datastore trigger that is
                    # the row owner, who is not the schedule owner.
                    user_id=getattr(schedule_run, "user_id", None) or schedule.user_id,
                )
            ]
        )

        await self._apply_breaker(schedule)
        return True

    async def record_filtered(
        self,
        schedule: ScheduleEntity,
        *,
        source_event_id: str,
        user_id: UUID,
        metadata: Mapping[str, object] | None,
        llm_output: Mapping[str, object],
    ) -> bool:
        """Record an event the schedule's filter turned down (PS-SCHED-012).

        A skip is recorded as a run of its own, ``FILTERED``, carrying the
        decision in its ``llm_output``, so a person can tell it apart from an
        event that never arrived and from one that failed. It is not counted on
        the breaker either way: nothing ran, so nothing succeeded or failed.

        ``last_fire_status`` moves only with a new row. A redelivered event
        already has its row, and stamping it again would report an old skip as
        the latest thing the schedule did.
        """
        recorded = await self.run_repository.record_filtered(
            schedule_id=schedule.id,
            user_id=user_id,
            source_event_id=source_event_id,
            target_kind=_target_kind(schedule),
            metadata=metadata,
            llm_output=llm_output,
        )
        if recorded:
            await self.schedule_repository.record_fire(
                schedule.id, status=ScheduleFireStatus.FILTERED
            )
        return recorded

    async def record_filter_undecided(
        self,
        schedule: ScheduleEntity,
        *,
        source_event_id: str,
        decision_id: UUID | None,
        user_id: UUID | None = None,
        metadata: Mapping[str, object] | None = None,
        error_type: str = FILTER_UNDECIDED,
    ) -> bool:
        """Record an event the filter or triage could not decide about, as a failed fire.

        PS-SCHED-012: a filter that fails to evaluate fails the trigger rather
        than skipping it, and a triage is held to the same. Dead-lettered like
        any failure that never reached a target, and for the same reason: the
        decision is recorded under the event, so a retry would read back the
        same open question. `decision_id` is None only when there was nothing
        to ask -- a triage whose decider is gone.
        """
        recorded = await self.record_pre_dispatch_failure(
            schedule,
            source_event_id=source_event_id,
            error_type=error_type,
            user_id=user_id,
            metadata=metadata,
            llm_output=(
                {"decision_id": str(decision_id)} if decision_id is not None else {}
            ),
        )
        if recorded:
            explanation = _EXPLANATIONS.get(error_type, _EXPLANATIONS[FILTER_UNDECIDED])
            await self.schedule_repository.record_fire(
                schedule.id,
                status=ScheduleFireStatus.ERROR,
                error=(
                    f"{explanation} (decision {decision_id})."
                    if decision_id is not None
                    else f"{explanation}."
                ),
            )
        return recorded

    async def record_held(
        self,
        schedule: ScheduleEntity,
        verdict: TriageVerdict,
        *,
        source_event_id: str,
        user_id: UUID,
        payload: Mapping[str, object],
        metadata: Mapping[str, object] | None,
    ) -> ScheduleRunEntity | None:
        """Hold an event the triage routed to its digest or to a person.

        Written once per source event, as a skip is: a redelivered event finds
        the row it has and changes nothing. Returned as it stands, which is
        what tells the caller whether a question is still owed on it. Not
        counted on the breaker -- nothing has run yet.
        """
        held = await HeldRunRepository(self.uow).hold(
            schedule_id=schedule.id,
            user_id=user_id,
            source_event_id=source_event_id,
            target_kind=_target_kind(schedule),
            held_for=verdict.route,
            payload=_json(payload),
            metadata=_json(metadata or {}),
            llm_output=verdict.output,
        )
        if held is None:
            return None
        run, created = held
        if created:
            await self.schedule_repository.record_fire(
                schedule.id, status=ScheduleFireStatus.HELD
            )
        return run

    async def record_pre_dispatch_failure(
        self,
        schedule: ScheduleEntity,
        *,
        source_event_id: str,
        error_type: str,
        user_id: UUID | None = None,
        metadata: Mapping[str, object] | None = None,
        llm_output: Mapping[str, object] | None = None,
    ) -> bool:
        """Record a fire that never reached a target, and count it on the breaker.

        The ledger is written when a fire is *dispatched*, so a fire that failed
        before that — the LLM filter could not run because the pod is out of
        budget — left no row at all. The breaker counts rows, so nothing counted
        it, and the schedule retried indefinitely with no ceiling and nothing to
        show the owner. Thirty of those in a day came from one pod.

        Dead-lettered rather than failed: ``consecutive_terminal_failures``
        deliberately ignores FAILED, which is the retryable intermediate state,
        and a fire whose budget has run out is not going to succeed by being
        tried again inside the same window. Five of them trips the breaker,
        deactivates the schedule, and emails the owner — which is the only part
        of this the user ever sees.

        Returns whether a new failure was recorded; a repeat of the same
        ``source_event_id`` is a no-op, so a redelivery cannot inflate the streak.

        ``user_id`` is the run's owner when it is not the schedule's -- the row
        owner of an RLS datastore event -- so the failure is listed to the same
        people a run of that fire would have been.
        """
        run = await self.run_repository.claim(
            schedule_id=schedule.id,
            user_id=user_id or schedule.user_id,
            source_event_id=source_event_id,
            target_kind=_target_kind(schedule),
            payload={},
            metadata=dict(metadata) if metadata is not None else None,
            llm_output=dict(llm_output) if llm_output is not None else None,
        )
        if run is None:
            return False

        await self.run_repository.dead_letter(run.id, error_type=error_type)
        await self.recompute_breaker(schedule.id)
        return True

    async def record_dispatch_dead_letter(self, schedule: ScheduleEntity) -> None:
        """Count a delivery failure in the transaction that first dead-lettered it."""
        locked = await self.schedule_repository.get_for_update(schedule.id)
        if locked is None:
            raise LookupError(f"Schedule {schedule.id} disappeared during dispatch")
        await self._apply_breaker(locked)

    async def recompute_breaker(self, schedule_id: UUID) -> None:
        schedule = await self.schedule_repository.get_for_update(schedule_id)
        if schedule is None:
            raise LookupError(
                f"Schedule {schedule_id} disappeared during reconciliation"
            )
        await self._apply_breaker(schedule)

    async def reconcile_tripped_schedules(self) -> int:
        """Deactivate backfilled schedules already beyond the configured threshold."""
        threshold = schedule_settings.schedule_max_consecutive_failures
        if threshold <= 0:
            return 0

        schedules = await self.schedule_repository.lock_breaker_candidates(threshold)
        deactivated = 0
        for schedule in schedules:
            if await self._deactivate(schedule, schedule.consecutive_failures):
                deactivated += 1
        return deactivated

    async def _apply_breaker(
        self,
        schedule: ScheduleEntity,
    ) -> None:
        """Advance the breaker for the *schedule*, whoever owned the run.

        Deliberate: on a shared RLS table any pod user's rows can drive runs, so
        a run owned by a row owner still counts toward the schedule owner's
        streak. The breaker protects the system from a persistently broken
        target, which is a property of the schedule and not of whoever happened
        to insert the row. The trade-off is that one user's bad data can pause a
        schedule for everyone; the owner is emailed on deactivation and can
        reactivate. See
        ``test_five_row_owner_workflow_failures_deactivate_schedule_owner_schedule``.
        """
        count = await self.run_repository.consecutive_terminal_failures(schedule.id)
        await self.schedule_repository.set_consecutive_failures(schedule.id, count)
        threshold = schedule_settings.schedule_max_consecutive_failures
        if threshold <= 0 or count < threshold:
            return
        await self._deactivate(schedule, count)

    async def _deactivate(self, schedule: ScheduleEntity, count: int) -> bool:
        if not await self.schedule_repository.deactivate_if_active(schedule.id):
            return False

        self.uow.collect_events(
            [
                ScheduleDeactivated(
                    schedule_id=schedule.id,
                    user_id=schedule.user_id,
                    schedule_type=schedule.schedule_type,
                    consecutive_failures=count,
                )
            ]
        )
        logger.warning(
            "schedule.breaker.tripped",
            schedule_id=str(schedule.id),
            consecutive_failures=count,
        )
        return True
