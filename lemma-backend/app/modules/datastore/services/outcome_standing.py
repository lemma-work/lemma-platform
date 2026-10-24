"""Where a pod stands against what it owns: the number now, and its weeks.

The number now is the measure read as the page asks for it. The weeks are
what the measure answered in each of them, kept by ``record_week`` -- every
reading replaces its week's, so a week shows the last one it got. Both read
every row of the pod, so everyone in it sees the same number; who may set a
measure is decided when it is set (``outcome_measure``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from app.core.ports.outcome_measures import MeasureReading
from app.modules.datastore.infrastructure.repositories.outcome_week_repository import (
    OutcomeWeek,
    OutcomeWeekRepository,
)
from app.modules.datastore.services.outcome_measure import (
    OutcomeMeasureService,
    measure_digest,
)
from app.modules.pod.contracts.outcome import PodOutcome

#: How many weeks a chart of them shows unless it asks for another number.
WEEKS_SHOWN = 8
#: A year: enough for any chart, and a bound on what one read returns.
MAX_WEEKS = 52

type OutcomeReader = Callable[[UUID], Awaitable[PodOutcome | None]]
type Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def week_starting(day: date) -> date:
    """The Monday of ``day``'s week."""
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True, slots=True)
class OutcomeStanding:
    outcome: PodOutcome | None
    reading: MeasureReading | None = None
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class WeekReading:
    """A reading taken for its week, not kept yet."""

    pod_id: UUID
    measure_digest: str
    #: Whether the measure answered; ``False`` with ``week.problem`` saying why.
    read: bool
    week: OutcomeWeek


@dataclass(frozen=True, slots=True)
class WeekSlot:
    """One week of a history: its last reading, or nothing for a week the
    measure was never read in."""

    starts_on: date
    week: OutcomeWeek | None


class OutcomeStandingService:
    def __init__(
        self,
        *,
        outcomes: OutcomeReader,
        measures: OutcomeMeasureService,
        weeks: OutcomeWeekRepository,
        clock: Clock = _utcnow,
    ) -> None:
        self._outcomes = outcomes
        self._measures = measures
        self._weeks = weeks
        self._clock = clock

    async def standing(self, pod_id: UUID) -> OutcomeStanding:
        """What the pod owns and what its measure answers now."""
        outcome = await self._outcomes(pod_id)
        if outcome is None or outcome.measure is None:
            return OutcomeStanding(outcome=outcome)
        result = await self._measures.read(pod_id=pod_id, sql=outcome.measure)
        return OutcomeStanding(
            outcome=outcome, reading=result.reading, problem=result.problem
        )

    async def history(
        self, pod_id: UUID, *, weeks: int = WEEKS_SHOWN
    ) -> list[WeekSlot]:
        """The last ``weeks`` weeks, oldest first and ending with this one.

        Only the weeks the pod's current measure answered: a week read by a
        measure it has since replaced is an empty slot, not another count.
        """
        this_week = week_starting(self._clock().date())
        mondays = [
            this_week - timedelta(weeks=back) for back in range(weeks - 1, -1, -1)
        ]
        outcome = await self._outcomes(pod_id)
        if outcome is None or outcome.measure is None:
            return [WeekSlot(starts_on=monday, week=None) for monday in mondays]
        found = await self._weeks.weeks(
            pod_id=pod_id,
            measure_digest=measure_digest(outcome.measure),
            since=mondays[0],
            limit=weeks,
        )
        return [
            WeekSlot(starts_on=monday, week=found.get(monday)) for monday in mondays
        ]

    async def read_week(self, pod_id: UUID) -> WeekReading | None:
        """This week's reading of the pod's measure; ``None`` when it has none.

        Separate from ``keep_week`` so a job can read in one short transaction
        and keep what it read in its fenced completion.
        """
        outcome = await self._outcomes(pod_id)
        if outcome is None or outcome.measure is None:
            return None
        result = await self._measures.read(pod_id=pod_id, sql=outcome.measure)
        measured_at = result.reading.measured_at if result.reading else self._clock()
        return WeekReading(
            pod_id=pod_id,
            measure_digest=measure_digest(outcome.measure),
            read=result.reading is not None,
            week=OutcomeWeek(
                starts_on=week_starting(measured_at.date()),
                value=result.reading.value if result.reading else None,
                target=outcome.target,
                problem=result.problem,
                measured_at=measured_at,
            ),
        )

    async def keep_week(self, reading: WeekReading) -> None:
        """Keep a reading as its week's.

        One that failed keeps the week's last number and says why beside it,
        so a measure that breaks on a Thursday leaves the week on what it
        answered on Wednesday rather than on nothing.
        """
        keep = self._weeks.record if reading.read else self._weeks.record_problem
        await keep(
            pod_id=reading.pod_id,
            measure_digest=reading.measure_digest,
            week=reading.week,
        )


__all__ = [
    "MAX_WEEKS",
    "WEEKS_SHOWN",
    "OutcomeStanding",
    "OutcomeStandingService",
    "WeekReading",
    "WeekSlot",
    "week_starting",
]
