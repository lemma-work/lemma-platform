"""The sweep that repairs a run ledger the event path left wrong.

Every dispatch is supposed to report its own outcome. This is what runs when
one does not: a lost dispatch, a target deleted under a run, a fire whose
moment has passed. It reads its targets through `workflow` and `agent`
contracts rather than their tables, so the only thing it knows about another
module is the question it is asking.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import Row, or_, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.conversation_outcomes import (
    load_conversation_outcomes,
)
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.contracts.target_outcome import TargetRunOutcome
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)
from app.modules.workflow.contracts.run_outcomes import load_run_outcomes

WORKFLOW_TARGET = "WORKFLOW"
AGENT_TARGET = "AGENT"

#: The two kinds of thing a schedule dispatches at. A run naming anything else
#: has no target to resolve, so it is treated as one that is gone.
_TARGET_KINDS = frozenset({WORKFLOW_TARGET, AGENT_TARGET})

#: What each verdict is counted as. `still_running` is here so that a row the
#: sweep looked at and correctly left alone cannot be reported as work; folding
#: it into `reconciled` is what made a sweep that did nothing at all report
#: `reconciled=100` on four hundred consecutive ticks.
_RECONCILED = "reconciled"
_REDELIVERED = "redelivered"
_DEAD_LETTERED = "dead_lettered"
_STILL_RUNNING = "still_running"

#: The two verdicts that say this fire produced nothing. Both count against the
#: schedule's breaker; the other two do not.
_COUNTS_ON_BREAKER = frozenset({_RECONCILED, _DEAD_LETTERED})


@dataclass(frozen=True, slots=True)
class ScheduleRunRecoveryResult:
    redelivered: int = 0
    reconciled: int = 0
    dead_lettered: int = 0
    # Rows looked at and correctly left alone: a target still running, or a
    # human form nobody has filled in yet.
    still_running: int = 0


class ScheduleRunRecoveryService:
    BATCH_SIZE = 100
    DISPATCH_RECONCILE_AFTER = timedelta(minutes=5)

    # A fire whose moment has passed by more than this is dead-lettered instead
    # of redelivered. Redelivery exists for a dispatch that was genuinely lost
    # moments ago; replaying a schedule from a month back does not produce the
    # run the user wanted, it produces a surprise -- 245 of them, in the
    # backlog this was found in, each one able to start a real agent run and
    # spend real quota.
    MAX_REDELIVERY_AGE = timedelta(hours=6)

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session

    async def recover(self, *, limit: int = BATCH_SIZE) -> ScheduleRunRecoveryResult:
        now = datetime.now(timezone.utc)
        rows = await self._claim_due_rows(limit=limit, now=now)
        outcomes = await self._target_outcomes(rows)

        tally: Counter[str] = Counter()
        breaker_schedule_ids: set[UUID] = set()
        for run, schedule in rows:
            # Stamped for every row the sweep reaches, whatever it decides. This
            # is what advances the cursor: the branches below may legitimately
            # change nothing, and a row that records no change is a row the next
            # tick selects again.
            #
            # It costs a real write, and the write is the point. `updated_at`
            # carries an `onupdate`, so stamping this bumps that too, and the
            # ledger now takes roughly one UPDATE per inspected still-running
            # row per re-inspection interval -- with the batch at 100 and the
            # sweep at 5 minutes, at most 28,800 a day whatever the backlog
            # does. Worth naming precisely, because "28,800 pointless updates a
            # day" is what the audit that started this reported. That claim was
            # wrong about the code as it stood -- the count was zero, and being
            # zero was the bug -- but it is a fair description of the code after
            # this change. The difference is that these updates are not
            # pointless: each one is the cursor moving past a row, which is the
            # only reason the sweep reaches the row behind it.
            run.last_inspected_at = now
            verdict = self._settle(run, schedule, now, outcomes.get(run.id))
            tally[verdict] += 1
            if verdict in _COUNTS_ON_BREAKER:
                breaker_schedule_ids.add(schedule.id)

        await self.session.flush()
        outcome_service = ScheduleRunOutcomeService(self.uow)
        for schedule_id in breaker_schedule_ids:
            await outcome_service.recompute_breaker(schedule_id)

        return ScheduleRunRecoveryResult(
            redelivered=tally[_REDELIVERED],
            reconciled=tally[_RECONCILED],
            dead_lettered=tally[_DEAD_LETTERED],
            still_running=tally[_STILL_RUNNING],
        )

    async def _claim_due_rows(
        self, *, limit: int, now: datetime
    ) -> list[Row[tuple[ScheduleRun, Schedule]]]:
        """Lock the next batch of runs whose outcome nothing has recorded."""
        retry_cutoff = now - ScheduleRunRepository.ABANDON_AFTER
        dispatch_cutoff = now - self.DISPATCH_RECONCILE_AFTER
        reinspect_cutoff = now - timedelta(
            minutes=schedule_settings.schedule_run_reinspect_after_minutes
        )
        result = await self.session.execute(
            select(ScheduleRun, Schedule)
            .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
            .where(
                Schedule.is_active.is_(True),
                ScheduleRun.target_outcome.is_(None),
                # Rows already looked at recently are skipped rather than
                # re-read every tick. Without this the sweep spends its
                # whole batch on the same long-lived in-flight runs and
                # never reaches anything behind them.
                #
                # This does set the worst case on noticing a *lost* outcome
                # event, which is the sweep's whole reason for existing, so
                # it is worth being exact about the cost. A run is only
                # skipped once it has been inspected and found still
                # running, so the delay is one re-inspection interval, not
                # the run's whole life. And the interval is not the binding
                # constraint anyway: at 100 rows per tick every 5 minutes,
                # the standing backlog of runs parked on human form waits
                # takes the sweep over an hour to work through on its own.
                # Before this filter existed the sweep was slower still,
                # because it spent those
                # ticks re-reading the same head of the queue. The ordering
                # below is what actually bounds the delay; this only stops a
                # small in-flight set from being re-read every 5 minutes for
                # no new information.
                or_(
                    ScheduleRun.last_inspected_at.is_(None),
                    ScheduleRun.last_inspected_at < reinspect_cutoff,
                ),
                or_(
                    (ScheduleRun.status == ScheduleRunStatus.PROCESSING.value)
                    & or_(
                        ScheduleRun.started_at.is_(None),
                        ScheduleRun.started_at < retry_cutoff,
                    ),
                    (ScheduleRun.status == ScheduleRunStatus.FAILED.value)
                    & (ScheduleRun.updated_at < retry_cutoff),
                    (ScheduleRun.status == ScheduleRunStatus.DISPATCHED.value)
                    & (ScheduleRun.updated_at < dispatch_cutoff),
                ),
            )
            # NULLS FIRST is explicit because Postgres does the opposite by
            # default: ASC sorts nulls *last*. Left implicit, a row the
            # sweep had already stamped would sort ahead of one it had never
            # looked at, so the batch it just finished would jump the queue
            # the moment it became re-eligible, while never-inspected rows
            # waited behind it. Never-inspected is exactly the case with the
            # most to tell us.
            .order_by(ScheduleRun.last_inspected_at.asc().nullsfirst(), ScheduleRun.id)
            .limit(max(1, min(limit, self.BATCH_SIZE)))
            .with_for_update(skip_locked=True, of=ScheduleRun)
        )
        return list(result.all())

    async def _target_outcomes(
        self, rows: list[Row[tuple[ScheduleRun, Schedule]]]
    ) -> dict[UUID, TargetRunOutcome]:
        """Each inspected run's target state, keyed by the *run* it belongs to.

        Two queries for the whole batch, one per target kind. Keyed by the
        schedule run rather than by the target so that the two kinds cannot
        collide in one mapping, and so the loop above asks about the row it is
        holding rather than re-deriving an id it already discarded.
        """
        targets: dict[UUID, tuple[str, UUID]] = {}
        for run, _schedule in rows:
            target_id = _as_uuid(run.target_run_id)
            if target_id is not None and run.target_kind in _TARGET_KINDS:
                targets[run.id] = (run.target_kind, target_id)

        loaded = {
            WORKFLOW_TARGET: await load_run_outcomes(
                self.uow, _ids_of_kind(targets, WORKFLOW_TARGET)
            ),
            AGENT_TARGET: await load_conversation_outcomes(
                self.uow, _ids_of_kind(targets, AGENT_TARGET)
            ),
        }
        return {
            run_id: loaded[kind][target_id]
            for run_id, (kind, target_id) in targets.items()
            if target_id in loaded[kind]
        }

    def _settle(
        self,
        run: ScheduleRun,
        schedule: Schedule,
        now: datetime,
        target: TargetRunOutcome | None,
    ) -> str:
        """Decide this row's fate and write it, returning what was decided."""
        if target is not None:
            return self._settle_against_target(run, target, now)
        abandon_reason = self._abandon_reason(run, schedule, now)
        if abandon_reason is not None:
            run.status = ScheduleRunStatus.DEAD_LETTERED.value
            run.completed_at = now
            run.error_type = abandon_reason
            return _DEAD_LETTERED
        self._redeliver(run, schedule)
        return _REDELIVERED

    def _settle_against_target(
        self, run: ScheduleRun, target: TargetRunOutcome, now: datetime
    ) -> str:
        outcome = _target_outcome(target.status)
        if outcome is None:
            self._repair_in_flight(run)
            return _STILL_RUNNING
        run.status = ScheduleRunStatus.DISPATCHED.value
        run.target_outcome = outcome.value
        run.completed_at = target.ended_at or now
        run.error_type = (
            f"{run.target_kind.title()}TargetFailed"
            if outcome == ScheduleRunStatus.TARGET_FAILED
            else None
        )
        run.error_code = None
        return _RECONCILED

    @staticmethod
    def _repair_in_flight(run: ScheduleRun) -> None:
        """Straighten a row whose target is alive and has not finished.

        Nothing is written when the ledger is already right. The write bumps
        `updated_at`, the DISPATCHED arm selects on
        `updated_at < now - DISPATCH_RECONCILE_AFTER`, and that window is
        exactly the cron interval -- so an unconditional write here re-armed the
        row for the very next pass, forever. At BATCH_SIZE rows and a
        five-minute cron that is 28,800 updates a day, none of which could move
        a row out of the query that found it.

        The repair is still made when there is one: a row left FAILED or
        half-started while its target really runs.
        """
        needs_repair = (
            run.status != ScheduleRunStatus.DISPATCHED.value
            or run.completed_at is not None
            or run.error_type is not None
            or run.error_code is not None
        )
        if not needs_repair:
            return
        run.status = ScheduleRunStatus.DISPATCHED.value
        run.completed_at = None
        run.error_type = None
        run.error_code = None

    def _abandon_reason(
        self, run: ScheduleRun, schedule: Schedule, now: datetime
    ) -> str | None:
        """Why this fire will never be replayed, or ``None`` to replay it.

        Every reason here counts on the breaker, which is worth stating because
        the obvious objection is that a deleted target is the user's doing and
        not the schedule's fault. The breaker does not ask whose fault it is; it
        asks whether this schedule is still capable of producing runs. A fire
        that reached no target and is now too old to replay produced nothing,
        and five of those in a row means the schedule has produced nothing five
        times running. Excluding them would leave a schedule that cannot
        possibly succeed, retrying on a timer, with nothing surfaced to the
        owner. The deactivation email is what turns it into something they can
        act on -- and reactivating is one click if they disagree.
        """
        if self._too_late_to_redeliver(run, now):
            return "ScheduleRunStale"
        if run.attempts >= ScheduleRunRepository.MAX_ATTEMPTS:
            return run.error_type or "ScheduleDispatchExhausted"
        if run.user_id is None and schedule.schedule_type == ScheduleType.DATASTORE:
            # A datastore schedule fires for whoever wrote the row, so there is
            # no owner to fall back to the way a time schedule has one.
            return "ScheduleRunOwnerMissing"
        return None

    def _too_late_to_redeliver(self, run: ScheduleRun, now: datetime) -> bool:
        """Whether this fire's moment has passed far enough to abandon it.

        Redelivery answers "the dispatch was lost, send it again". It is the
        wrong answer to "the target was deleted a month ago": the scheduled
        moment is long gone, and re-firing produces a run nobody asked for that
        spends real quota. Age is measured from the scheduled time where there
        is one, and from row creation otherwise.
        """
        fired_at = run.source_occurred_at or run.created_at
        if fired_at is None:
            return False
        if fired_at.tzinfo is None:
            fired_at = fired_at.replace(tzinfo=timezone.utc)
        return now - fired_at > self.MAX_REDELIVERY_AGE

    def _redeliver(self, run: ScheduleRun, schedule: Schedule) -> None:
        """Re-arm the row and publish the fire again."""
        if run.user_id is None:
            run.user_id = schedule.user_id
        if run.target_run_id is None:
            run.target_run_id = str(uuid7())

        run.status = ScheduleRunStatus.RECEIVED.value
        run.started_at = None
        run.completed_at = None
        run.error_type = None
        run.error_code = None
        self.uow.collect_events(
            [
                ScheduleFired(
                    schedule_id=schedule.id,
                    user_id=run.user_id,
                    schedule_type=schedule.schedule_type,
                    pod_id=schedule.pod_id,
                    account_id=schedule.account_id,
                    payload=run.payload or {},
                    metadata=run.fire_metadata or {},
                    llm_output=run.llm_output or {},
                    scheduled_at=run.source_occurred_at,
                    source_event_id=run.source_event_id,
                    causation_id=run.id,
                )
            ]
        )


def _ids_of_kind(targets: dict[UUID, tuple[str, UUID]], kind: str) -> set[UUID]:
    return {
        target_id for target_kind, target_id in targets.values() if target_kind == kind
    }


def _as_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except TypeError, ValueError:
        return None


def _target_outcome(status: str | None) -> ScheduleRunStatus | None:
    return {
        "COMPLETED": ScheduleRunStatus.COMPLETED,
        "FAILED": ScheduleRunStatus.TARGET_FAILED,
        "CANCELLED": ScheduleRunStatus.CANCELLED,
        "STOPPED": ScheduleRunStatus.CANCELLED,
    }.get(status or "")
