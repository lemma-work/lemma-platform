"""Where a pod stands: one number, read for everyone, and the weeks behind it."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.ports.outcome_measures import MeasureReading
from app.modules.datastore.domain.errors import DatastoreMeasureError
from app.modules.datastore.infrastructure.repositories.outcome_week_repository import (
    OutcomeWeek,
)
from app.modules.datastore.services.outcome_measure import (
    MeasureResult,
    tables_counted,
    measure_digest,
    the_number,
)
from app.modules.datastore.services.outcome_standing import (
    OutcomeStandingService,
    week_starting,
)
from app.modules.pod.contracts.outcome import PodOutcome

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)  # a Thursday
MEASURE = "SELECT count(*) FROM posts WHERE published_at >= date_trunc('week', now())"


def test_a_measure_answers_one_row_of_one_number_or_null():
    assert the_number([{"count": 3}]) == 3.0
    assert the_number([{"share": Decimal("87.5")}]) == 87.5
    assert the_number([{"share": None}]) is None


@pytest.mark.parametrize(
    ("rows", "said"),
    [
        ([], "0 rows"),
        ([{"n": 1}, {"n": 2}], "2 rows"),
        ([{"done": 1, "total": 2}], "2 columns"),
        ([{"n": "12"}], "not a number"),
        ([{"n": True}], "not a number"),
        ([{"n": float("nan")}], "no value"),
    ],
)
def test_anything_else_is_refused_by_what_it_answered(rows, said):
    with pytest.raises(DatastoreMeasureError, match=said):
        the_number(rows)


def test_any_edit_to_a_query_is_another_measure():
    assert measure_digest("SELECT 1 FROM t\n") == measure_digest("SELECT 1 FROM t")
    assert measure_digest("SELECT 'a  b' FROM t") != measure_digest(
        "SELECT 'a b' FROM t"
    )


@pytest.mark.parametrize(
    "sql",
    [
        # A CTE named like a real table stands for it only in its own query.
        (
            "SELECT count(*) FROM mine WHERE (SELECT count(*) FROM secret) > 0 "
            "AND EXISTS (WITH secret AS (SELECT 1) SELECT 1 FROM secret)"
        ),
        "WITH secret AS (SELECT * FROM secret) SELECT count(*) FROM mine, secret",
        "SELECT count(*) FROM mine, LATERAL (SELECT 1 FROM secret) AS s",
    ],
)
def test_every_table_a_measure_reads_is_counted_as_one_it_reads(sql):
    assert tables_counted(sql) == {"mine", "secret"}


def test_a_measure_names_tables_as_the_database_folds_them():
    assert tables_counted('SELECT count(*) FROM "Mixed" JOIN Lower ON true') == {
        "Mixed",
        "lower",
    }


def test_weeks_start_on_monday():
    assert week_starting(date(2026, 10, 22)) == date(2026, 10, 19)
    assert week_starting(date(2026, 10, 19)) == date(2026, 10, 19)


class _MemoryCache:
    """Redis, as the standing cache uses it, in this process."""

    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get_json(self, suffix: str) -> object | None:
        return self.values.get(suffix)

    async def set_json(self, suffix: str, value: object) -> None:
        self.values[suffix] = value


class _Measures:
    def __init__(
        self, result: MeasureResult, refused: MeasureResult | None = None
    ) -> None:
        self.result = result
        self.refused = refused
        self.reads = 0

    async def refusal(
        self, *, pod_id: UUID, sql: str, checked: object
    ) -> MeasureResult | None:
        del pod_id, sql, checked
        return self.refused

    async def read(self, *, pod_id: UUID, sql: str, checked: object) -> MeasureResult:
        del pod_id, sql, checked
        self.reads += 1
        return self.result


class _Weeks:
    """The week rows, keyed as the table keys them."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, date], tuple[str, OutcomeWeek]] = {}

    async def record(
        self, *, pod_id: UUID, measure_digest: str, week: OutcomeWeek
    ) -> None:
        self.rows[(pod_id, week.starts_on)] = (measure_digest, week)

    async def record_problem(
        self, *, pod_id: UUID, measure_digest: str, week: OutcomeWeek
    ) -> None:
        digest, before = self.rows.get((pod_id, week.starts_on), (None, None))
        if before is not None and digest == measure_digest:
            week = OutcomeWeek(
                starts_on=week.starts_on,
                value=before.value,
                target=before.target,
                problem=week.problem,
                measured_at=before.measured_at,
            )
        self.rows[(pod_id, week.starts_on)] = (measure_digest, week)

    async def weeks(
        self, *, pod_id: UUID, measure_digest: str, since: date, limit: int
    ) -> dict[date, OutcomeWeek]:
        del limit
        return {
            starts_on: week
            for (pod, starts_on), (digest, week) in self.rows.items()
            if pod == pod_id and digest == measure_digest and starts_on >= since
        }


def _service(
    outcome: PodOutcome | None,
    result: MeasureResult,
    weeks: _Weeks | None = None,
    readings: _MemoryCache | None = None,
    measures: _Measures | None = None,
) -> OutcomeStandingService:
    async def outcome_of(pod_id: UUID) -> PodOutcome | None:
        del pod_id
        return outcome

    return OutcomeStandingService(
        outcomes=outcome_of,
        measures=measures or _Measures(result),  # type: ignore[arg-type]
        weeks=weeks or _Weeks(),  # type: ignore[arg-type]
        clock=lambda: NOW,
        readings=readings or _MemoryCache(),
    )


def _owns(measure: str | None = MEASURE) -> PodOutcome:
    return PodOutcome.model_validate(
        {
            "sentence": "Two posts go out every week.",
            "kind": "week",
            "target": 2,
            "unit": "posts",
            "measure": measure,
        }
    )


def _read(value: float | None) -> MeasureResult:
    return MeasureResult(reading=MeasureReading(value=value, measured_at=NOW))


@pytest.mark.asyncio
async def test_standing_is_the_number_now_or_why_there_is_none():
    standing = await _service(_owns(), _read(1)).standing(uuid4())
    assert standing.reading is not None and standing.reading.value == 1
    broken = await _service(
        _owns(), MeasureResult(reading=None, problem="gone")
    ).standing(uuid4())
    assert (broken.reading, broken.problem) == (None, "gone")
    unmeasured = await _service(_owns(None), _read(1)).standing(uuid4())
    assert unmeasured.reading is None and unmeasured.outcome is not None


@pytest.mark.asyncio
async def test_a_weeks_last_reading_is_its_number_and_history_ends_this_week():
    pod, weeks = uuid4(), _Weeks()
    for value in (1, 2):
        service = _service(_owns(), _read(value), weeks)
        reading = await service.read_week(pod)
        assert reading is not None
        await service.keep_week(reading)
    history = await _service(_owns(), _read(0), weeks).history(pod, weeks=8)
    assert [slot.starts_on for slot in history][-1] == date(2026, 10, 19)
    assert len(history) == 8 and history[0].starts_on == date(2026, 8, 31)
    assert history[-1].week is not None and history[-1].week.value == 2
    assert all(slot.week is None for slot in history[:-1])


@pytest.mark.asyncio
async def test_a_failed_reading_keeps_the_weeks_number_and_says_why():
    pod, weeks = uuid4(), _Weeks()
    good = _service(_owns(), _read(2), weeks)
    await good.keep_week(await good.read_week(pod))  # type: ignore[arg-type]
    bad = _service(_owns(), MeasureResult(reading=None, problem="too slow"), weeks)
    await bad.keep_week(await bad.read_week(pod))  # type: ignore[arg-type]
    (slot,) = await good.history(pod, weeks=1)
    assert slot.week is not None
    assert (slot.week.value, slot.week.problem) == (2, "too slow")


@pytest.mark.asyncio
async def test_a_new_measure_starts_a_new_history():
    pod, weeks = uuid4(), _Weeks()
    old = _service(_owns(), _read(2), weeks)
    await old.keep_week(await old.read_week(pod))  # type: ignore[arg-type]
    changed = _service(_owns("SELECT count(*) FROM posts"), _read(9), weeks)
    (slot,) = await changed.history(pod, weeks=1)
    assert slot.week is None
    putting_it_back = await old.history(pod, weeks=1)
    assert putting_it_back[0].week is not None


@pytest.mark.asyncio
async def test_nothing_is_read_for_a_pod_with_no_measure():
    assert await _service(_owns(None), _read(1)).read_week(uuid4()) is None
    assert await _service(None, _read(1)).read_week(uuid4()) is None
    history = await _service(None, _read(1)).history(uuid4(), weeks=3)
    assert [slot.week for slot in history] == [None, None, None]
    assert history[0].starts_on == date(2026, 10, 19) - timedelta(weeks=2)


@pytest.mark.asyncio
async def test_the_number_now_is_read_once_for_everyone_asking_within_the_window():
    """Any member can ask, and a page may poll, while the measure reads every
    row of its tables: so a pod's number is read once and served from the
    cache to the asks that follow. A new measure is another entry at once."""
    pod_id = uuid4()
    readings = _MemoryCache()
    service = _service(_owns(), _read(3), readings=readings)
    measures = service._measures  # type: ignore[attr-defined]

    first = await service.standing(pod_id)
    again = await service.standing(pod_id)

    assert measures.reads == 1
    assert first.reading == again.reading
    edited = _service(_owns(MEASURE + " AND true"), _read(4), readings=readings)
    assert (await edited.standing(pod_id)).reading.value == 4


@pytest.mark.asyncio
async def test_a_number_already_cached_is_not_served_once_it_is_refused():
    """Row security turned on under a measure stops the number at once, not
    when the cached one runs out."""
    pod, cache = uuid4(), _MemoryCache()
    assert (await _service(_owns(), _read(7), readings=cache).standing(pod)).reading
    refused = MeasureResult(reading=None, problem="Row security is on for posts now")
    measures = _Measures(_read(7), refused=refused)
    standing = await _service(
        _owns(), _read(7), readings=cache, measures=measures
    ).standing(pod)
    assert (standing.reading, standing.problem) == (None, refused.problem)
    assert measures.reads == 0
