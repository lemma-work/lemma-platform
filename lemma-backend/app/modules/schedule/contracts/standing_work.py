"""Whether a pod's standing work ran when it was due, counted from the run ledger.

A teammate's scorecard has a reliability line -- "standing work runs on time" --
and the number behind it has to come from the ledger, not from the agent's
recollection of what it did. This is that number: of the runs that fell due in
a window, how many started within :data:`ON_TIME_ALLOWANCE` of their due time
and then completed.

Three things are left out, each for a reason.

* **Filtered runs.** A filter deciding a firing was not worth acting on is the
  schedule working, not standing work that failed to happen.
* **Redrives.** A person re-running a failed run does not make the occurrence
  due twice, and a redrive copies its original's due time -- counted, it would
  put one late occurrence into the total twice.
* **Internal schedules**, for the reason ``pod_summaries`` gives: they are
  workflow machinery, not anybody's standing work.

**Visibility is enforced, not assumed**, and the same way the pod's schedule
listing enforces it: a run counts only if this context may read its schedule
(:func:`readable_schedules`). A DATASTORE schedule narrows further. Each of its
runs is the reaction to somebody's row, and on a per-person table that row is
not everyone's to know about -- the run history shows another member's runs
only to a reader the table's policy allows. Here only the reader's own count.
That under-counts a shared table's runs, which is the safe direction: the
alternative tells one member how often another member's private rows change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.core.authorization.context import Context
from app.modules.schedule.contracts.pod_summaries import readable_schedules
from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule

#: How late a run may start and still count as on time. Long enough to absorb a
#: poller tick, a worker restart and a dispatch retry, which are the platform
#: being itself; short enough that a run outside it has missed what a person
#: scheduled it in time for -- the reason it was given a time at all.
ON_TIME_ALLOWANCE = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class StandingWorkCount:
    #: Runs that started within ``ON_TIME_ALLOWANCE`` of being due and completed.
    on_time: int
    #: Every run that fell due in the window, filtered runs excepted.
    due: int


def _effective_status() -> ColumnElement[str]:
    """The run's status as its history reports it: the target's, once known."""
    return func.coalesce(ScheduleRun.target_outcome, ScheduleRun.status)


def _visible_runs(ctx: Context) -> ColumnElement[bool]:
    """Every run of a readable schedule, save other people's row reactions.

    Without a person in the context there are no "own" runs to keep, and
    comparing to ``None`` would compile to ``IS NULL`` -- selecting exactly the
    ownerless runs the narrowing exists to withhold.
    """
    not_a_row_reaction = Schedule.schedule_type != ScheduleType.DATASTORE
    if ctx.user_id is None:
        return not_a_row_reaction
    return or_(not_a_row_reaction, ScheduleRun.user_id == ctx.user_id)


async def count_standing_work(
    *,
    session: AsyncSession,
    pod_id: UUID,
    ctx: Context,
    start: datetime,
    end: datetime,
) -> StandingWorkCount:
    """Runs of this pod's schedules that fell due in ``[start, end)``.

    "Due" is the run's ``source_occurred_at`` -- the occurrence it serves, not
    when the ledger row was written, which a backlog can put hours later.
    ``ctx`` is required for the reason ``list_schedule_summaries`` gives: an
    optional authorization context is one a caller forgets.
    """
    started_on_time = and_(
        _effective_status() == ScheduleRunStatus.COMPLETED.value,
        ScheduleRun.started_at.is_not(None),
        ScheduleRun.started_at <= ScheduleRun.source_occurred_at + ON_TIME_ALLOWANCE,
    )
    row = (
        await session.execute(
            select(func.count().filter(started_on_time), func.count())
            .select_from(ScheduleRun)
            .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
            .where(
                Schedule.pod_id == pod_id,
                Schedule.is_internal.is_(False),
                readable_schedules(ctx),
                _visible_runs(ctx),
                ScheduleRun.redrive_of_run_id.is_(None),
                ScheduleRun.source_occurred_at >= start,
                ScheduleRun.source_occurred_at < end,
                _effective_status() != ScheduleRunStatus.FILTERED.value,
            )
        )
    ).one()
    on_time, due = row
    return StandingWorkCount(on_time=int(on_time), due=int(due))


__all__ = ["ON_TIME_ALLOWANCE", "StandingWorkCount", "count_standing_work"]
