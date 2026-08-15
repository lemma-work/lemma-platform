"""Cross-module reconciliation for durable schedule target dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import or_, select
from sqlalchemy.orm import load_only

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.schedule.config import schedule_settings
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
from app.modules.workflow.infrastructure.models import WorkflowRunModel


@dataclass(frozen=True, slots=True)
class ScheduleRunRecoveryResult:
    redelivered: int = 0
    reconciled: int = 0
    dead_lettered: int = 0
    # Rows looked at and correctly left alone: a target still running, or a
    # human form nobody has filled in yet. Counted separately because folding
    # them into `reconciled` is what made a sweep that did nothing at all report
    # `reconciled=100` on four hundred consecutive ticks.
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
        retry_cutoff = now - ScheduleRunRepository.ABANDON_AFTER
        dispatch_cutoff = now - self.DISPATCH_RECONCILE_AFTER
        reinspect_cutoff = now - timedelta(
            minutes=schedule_settings.schedule_run_reinspect_after_minutes
        )
        rows = (
            await self.session.execute(
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
                    # constraint anyway: 100 rows per tick every 5 minutes over
                    # the ~1,375 rows production parks on human form waits is a
                    # ~69-minute round trip on its own. Before this filter
                    # existed the sweep was slower still, because it spent those
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
                .order_by(
                    ScheduleRun.last_inspected_at.asc().nullsfirst(), ScheduleRun.id
                )
                .limit(max(1, min(limit, self.BATCH_SIZE)))
                .with_for_update(skip_locked=True, of=ScheduleRun)
            )
        ).all()

        redelivered = 0
        reconciled = 0
        dead_lettered = 0
        still_running = 0
        breaker_schedule_ids: set[UUID] = set()

        await self._warm_targets(run for run, _ in rows)

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
            target_exists, outcome, completed_at = await self._resolve_target(run)
            if outcome is not None:
                run.status = ScheduleRunStatus.DISPATCHED.value
                run.target_outcome = outcome.value
                run.completed_at = completed_at or now
                run.error_type = (
                    f"{run.target_kind.title()}TargetFailed"
                    if outcome == ScheduleRunStatus.TARGET_FAILED
                    else None
                )
                run.error_code = None
                breaker_schedule_ids.add(schedule.id)
                reconciled += 1
                continue
            if target_exists:
                # The target is alive and has not finished. Nothing to reconcile
                # -- the ledger is already right -- so this is reported as what
                # it is rather than counted as work.
                run.status = ScheduleRunStatus.DISPATCHED.value
                run.completed_at = None
                run.error_type = None
                run.error_code = None
                still_running += 1
                continue

            if self._too_late_to_redeliver(run, now):
                # Counts on the breaker, which is worth stating because the
                # obvious objection is that a deleted target is the user's doing
                # and not the schedule's fault. The breaker does not ask whose
                # fault it is; it asks whether this schedule is still capable of
                # producing runs. A fire that reached no target and is now too
                # old to replay produced nothing, and five of those in a row
                # means the schedule has produced nothing five times running.
                # Excluding them would leave exactly the shape this PR keeps
                # finding elsewhere: a schedule that cannot possibly succeed,
                # retrying on a timer, with nothing surfaced to the owner. The
                # deactivation email is what turns it into something they can
                # act on -- and reactivating is one click if they disagree.
                run.status = ScheduleRunStatus.DEAD_LETTERED.value
                run.completed_at = now
                run.error_type = "ScheduleRunStale"
                breaker_schedule_ids.add(schedule.id)
                dead_lettered += 1
                continue

            if run.attempts >= ScheduleRunRepository.MAX_ATTEMPTS:
                run.status = ScheduleRunStatus.DEAD_LETTERED.value
                run.completed_at = now
                run.error_type = run.error_type or "ScheduleDispatchExhausted"
                breaker_schedule_ids.add(schedule.id)
                dead_lettered += 1
                continue

            if run.user_id is None:
                if schedule.schedule_type == ScheduleType.DATASTORE:
                    run.status = ScheduleRunStatus.DEAD_LETTERED.value
                    run.completed_at = now
                    run.error_type = "ScheduleRunOwnerMissing"
                    breaker_schedule_ids.add(schedule.id)
                    dead_lettered += 1
                    continue
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
            redelivered += 1

        await self.session.flush()
        outcome_service = ScheduleRunOutcomeService(self.uow)
        for schedule_id in breaker_schedule_ids:
            await outcome_service.recompute_breaker(schedule_id)

        return ScheduleRunRecoveryResult(
            redelivered=redelivered,
            reconciled=reconciled,
            dead_lettered=dead_lettered,
            still_running=still_running,
        )

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

    async def _warm_targets(self, runs) -> None:
        """Load every target this sweep will inspect, in two queries.

        ``_resolve_target`` reads two scalars per run but ``session.get`` loads
        the whole row, and a workflow run carries four JSONB columns including
        ``step_history``, which grows with every step the workflow took. At a
        hundred runs a tick that was a hundred round trips each dragging an
        unbounded payload to read a status and a timestamp.

        Batched by kind with a column projection, the identity map is warm
        before the loop starts, so the ``session.get`` calls below resolve
        locally. They stay as ``get`` on purpose: the projection means a miss
        would still be correct, just slow, rather than wrong.
        """
        by_kind: dict[str, set[UUID]] = {"WORKFLOW": set(), "AGENT": set()}
        for run in runs:
            target_ids = by_kind.get(run.target_kind)
            if target_ids is None or run.target_run_id is None:
                continue
            try:
                target_ids.add(UUID(str(run.target_run_id)))
            except TypeError, ValueError:
                continue

        for kind, model, columns in (
            (
                "WORKFLOW",
                WorkflowRunModel,
                (WorkflowRunModel.id, WorkflowRunModel.status, WorkflowRunModel.completed_at),
            ),
            (
                "AGENT",
                ConversationModel,
                (ConversationModel.id, ConversationModel.status, ConversationModel.updated_at),
            ),
        ):
            target_ids = by_kind[kind]
            if not target_ids:
                continue
            await self.session.execute(
                select(model)
                .where(model.id.in_(target_ids))
                .options(load_only(*columns))
            )

    async def _resolve_target(
        self, run: ScheduleRun
    ) -> tuple[bool, ScheduleRunStatus | None, datetime | None]:
        try:
            target_id = UUID(str(run.target_run_id))
        except TypeError, ValueError:
            return False, None, None

        if run.target_kind == "WORKFLOW":
            target = await self.session.get(WorkflowRunModel, target_id)
            if target is None:
                return False, None, None
            return True, _target_outcome(target.status), target.completed_at
        if run.target_kind == "AGENT":
            target = await self.session.get(ConversationModel, target_id)
            if target is None:
                return False, None, None
            return True, _target_outcome(target.status), target.updated_at
        return False, None, None


def _target_outcome(status: str | None) -> ScheduleRunStatus | None:
    return {
        "COMPLETED": ScheduleRunStatus.COMPLETED,
        "FAILED": ScheduleRunStatus.TARGET_FAILED,
        "CANCELLED": ScheduleRunStatus.CANCELLED,
        "STOPPED": ScheduleRunStatus.CANCELLED,
    }.get(status or "")
