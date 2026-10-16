"""The sweep that settles an agent run whose outcome event was lost.

Kept only until agent outcomes arrive as work-runtime facts, then deleted.

Everything else this sweep used to repair now has an owner that cannot lose
it. A reserved run is started by its ``schedule.start_target`` item, whose
lease retries a lost dispatch and whose dead hook records the one that never
starts; a workflow target reports its end as a ``WorkflowRunFinished`` fact in
the run's own transaction. An agent target is started in the transaction that
marks its schedule run DISPATCHED, but its end still travels on the
target-outcome stream -- and a message lost there leaves the run DISPATCHED
forever. That gap, and only that one, is what this closes: a DISPATCHED agent
run with no outcome, read against its conversation through the agent
contract.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Row, or_, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.conversation_outcomes import (
    load_conversation_outcomes,
)
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.contracts.target_outcome import TargetRunOutcome
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)

AGENT_TARGET = "AGENT"

#: The conversation a DISPATCHED run started is gone: nothing will ever end it.
TARGET_MISSING = "ScheduleTargetMissing"

#: What each verdict is counted as. `still_running` is here so that a row the
#: sweep looked at and correctly left alone cannot be reported as work.
_RECONCILED = "reconciled"
_DEAD_LETTERED = "dead_lettered"
_STILL_RUNNING = "still_running"

#: The verdicts that say this fire has ended. Both feed the schedule's
#: breaker; a run still going does not.
_COUNTS_ON_BREAKER = frozenset({_RECONCILED, _DEAD_LETTERED})


@dataclass(frozen=True, slots=True)
class ScheduleRunRecoveryResult:
    reconciled: int = 0
    dead_lettered: int = 0
    # Rows looked at and correctly left alone: an agent still running.
    still_running: int = 0


class ScheduleRunRecoveryService:
    BATCH_SIZE = 100
    DISPATCH_RECONCILE_AFTER = timedelta(minutes=5)

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
            # Stamped for every row the sweep reaches, whatever it decides: the
            # stamp is the cursor. A row whose target is still running records
            # no other change, and without it the next tick selects it again
            # and never reaches the rows behind it.
            run.last_inspected_at = now
            verdict = self._settle(run, now, outcomes.get(run.id))
            tally[verdict] += 1
            if verdict in _COUNTS_ON_BREAKER:
                breaker_schedule_ids.add(schedule.id)

        await self.session.flush()
        outcome_service = ScheduleRunOutcomeService(self.uow)
        for schedule_id in breaker_schedule_ids:
            await outcome_service.recompute_breaker(schedule_id)

        return ScheduleRunRecoveryResult(
            reconciled=tally[_RECONCILED],
            dead_lettered=tally[_DEAD_LETTERED],
            still_running=tally[_STILL_RUNNING],
        )

    async def _claim_due_rows(
        self, *, limit: int, now: datetime
    ) -> list[Row[tuple[ScheduleRun, Schedule]]]:
        """Lock the next batch of dispatched agent runs nothing has settled."""
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
                ScheduleRun.target_kind == AGENT_TARGET,
                ScheduleRun.status == ScheduleRunStatus.DISPATCHED.value,
                ScheduleRun.updated_at < dispatch_cutoff,
                # Rows already looked at recently are skipped rather than
                # re-read every tick, so a standing set of long-running agents
                # cannot hold the batch while lost outcomes wait behind it.
                or_(
                    ScheduleRun.last_inspected_at.is_(None),
                    ScheduleRun.last_inspected_at < reinspect_cutoff,
                ),
            )
            # NULLS FIRST is explicit because Postgres sorts nulls last on ASC:
            # never-inspected rows, the ones with the most to tell, go first.
            .order_by(ScheduleRun.last_inspected_at.asc().nullsfirst(), ScheduleRun.id)
            .limit(max(1, min(limit, self.BATCH_SIZE)))
            .with_for_update(skip_locked=True, of=ScheduleRun)
        )
        return list(result.all())

    async def _target_outcomes(
        self, rows: list[Row[tuple[ScheduleRun, Schedule]]]
    ) -> dict[UUID, TargetRunOutcome]:
        """Each inspected run's conversation state, keyed by the schedule run."""
        targets: dict[UUID, UUID] = {}
        for run, _schedule in rows:
            target_id = _as_uuid(run.target_run_id)
            if target_id is not None:
                targets[run.id] = target_id
        loaded = await load_conversation_outcomes(self.uow, set(targets.values()))
        return {
            run_id: loaded[target_id]
            for run_id, target_id in targets.items()
            if target_id in loaded
        }

    def _settle(
        self, run: ScheduleRun, now: datetime, target: TargetRunOutcome | None
    ) -> str:
        """Decide this row's fate and write it, returning what was decided."""
        if target is None:
            run.status = ScheduleRunStatus.DEAD_LETTERED.value
            run.completed_at = now
            run.error_type = TARGET_MISSING
            return _DEAD_LETTERED
        outcome = _target_outcome(target.status)
        if outcome is None:
            self._repair_in_flight(run)
            return _STILL_RUNNING
        run.target_outcome = outcome.value
        run.completed_at = target.ended_at or now
        run.error_type = (
            "AgentTargetFailed" if outcome == ScheduleRunStatus.TARGET_FAILED else None
        )
        run.error_code = None
        return _RECONCILED

    @staticmethod
    def _repair_in_flight(run: ScheduleRun) -> None:
        """Clear terminal fields on a row whose agent is alive and unfinished.

        Nothing is written when the ledger is already right: an unconditional
        write bumps `updated_at` and re-arms the row for the very next pass.
        """
        if (
            run.completed_at is None
            and run.error_type is None
            and run.error_code is None
        ):
            return
        run.completed_at = None
        run.error_type = None
        run.error_code = None


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
