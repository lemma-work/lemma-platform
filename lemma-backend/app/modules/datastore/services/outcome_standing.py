"""Where a pod stands against what it owns: the number now, and its weeks.

The number now is the measure read as the page asks for it. The weeks are
what the measure answered in each of them, kept by ``record_week`` -- every
reading replaces its week's, so a week shows the last one it got. Both read
every row of the pod, so everyone in it sees the same number; who may set a
measure is decided when it is set (``outcome_measure``), and a measure counting
a table whose row security that decision never covered is not read at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.ports.outcome_measures import MeasureReading
from app.modules.datastore.infrastructure.repositories.outcome_week_repository import (
    OutcomeWeek,
    OutcomeWeekRepository,
)
from app.modules.datastore.services.outcome_measure import (
    MeasureResult,
    OutcomeMeasureService,
    measure_digest,
)
from app.modules.pod.contracts.outcome import PodOutcome

#: How many weeks a chart of them shows unless it asks for another number.
WEEKS_SHOWN = 8
#: A year: enough for any chart, and a bound on what one read returns.
MAX_WEEKS = 52

#: How long the number now is served from Redis. Any member can ask for it,
#: and a page may poll it, while the measure reads every row of the tables it
#: names: so it is read at most this often per pod and measure. A new measure
#: has a new digest, and so a new entry, at once.
STANDING_CACHE_SECONDS = 30

type OutcomeReader = Callable[[UUID], Awaitable[PodOutcome | None]]
type Clock = Callable[[], datetime]

_standing_cache: RedisJsonCache[str] | None = None


def _standings() -> RedisJsonCache[str]:
    global _standing_cache
    if _standing_cache is None:
        _standing_cache = RedisJsonCache(
            settings.redis_url, "datastore:outcome-standing", STANDING_CACHE_SECONDS
        )
    return _standing_cache


class StandingCache(Protocol):
    """Where the number now is kept between asks: Redis, outside a test."""

    async def get_json(self, suffix: str) -> Any | None: ...

    async def set_json(self, suffix: str, value: Any) -> None: ...


async def _cached(cache: StandingCache, key: str) -> MeasureResult | None:
    try:
        raw = await cache.get_json(key)
    except RedisError, OSError:
        return None  # a miss: the measure is read instead
    if not isinstance(raw, dict):
        return None
    reading = None
    if raw.get("measured_at"):
        reading = MeasureReading(
            value=raw.get("value"),
            measured_at=datetime.fromisoformat(raw["measured_at"]),
        )
    return MeasureResult(reading=reading, problem=raw.get("problem"))


async def _keep(cache: StandingCache, key: str, result: MeasureResult) -> None:
    reading = result.reading
    try:
        await cache.set_json(
            key,
            {
                "value": reading.value if reading else None,
                "measured_at": reading.measured_at.isoformat() if reading else None,
                "problem": result.problem,
            },
        )
    except RedisError, OSError:
        return  # not kept; the next ask reads the measure again


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
        session: Any = None,
        readings: StandingCache | None = None,
    ) -> None:
        self._outcomes = outcomes
        self._measures = measures
        self._weeks = weeks
        self._clock = clock
        # The request's session, handed back while the cache is asked: Redis
        # is no reason to hold a pooled Postgres connection.
        self._session = session
        self._readings = readings

    async def standing(self, pod_id: UUID) -> OutcomeStanding:
        """What the pod owns and what its measure answers now."""
        outcome = await self._outcomes(pod_id)
        if outcome is None or outcome.measure is None:
            return OutcomeStanding(outcome=outcome)
        # Before the cache: a number read while the measure's tables had no
        # row security is not served once one of them has it.
        refused = await self._measures.refusal(
            pod_id=pod_id,
            sql=outcome.measure,
            checked=outcome.row_security_checked,
        )
        if refused is not None:
            return OutcomeStanding(
                outcome=outcome, reading=refused.reading, problem=refused.problem
            )
        key = f"{pod_id}:{measure_digest(outcome.measure)}"
        readings = self._readings or _standings()
        async with connection_released(self._session):
            result = await _cached(readings, key)
        if result is None:
            result = await self._measures.read(
                pod_id=pod_id,
                sql=outcome.measure,
                checked=outcome.row_security_checked,
            )
            async with connection_released(self._session):
                await _keep(readings, key, result)
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
        result = await self._measures.read(
            pod_id=pod_id, sql=outcome.measure, checked=outcome.row_security_checked
        )
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
