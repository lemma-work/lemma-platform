"""The scorecard preview and the rows behind a number, over a fake source.

What is pinned here is the choosing and the refusing: which measures a preview
counts, over which weeks and in which order, what a draft is refused for before
anything runs, and which measures have rows to open. The statements themselves
are ``test_scorecard_work``'s, and running them against real tables is the e2e
suite's.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.domain.errors import (
    ScorecardMeasureNotFoundError,
    ScorecardNotFoundError,
    ScorecardRefusedError,
)
from app.modules.agent.domain.scorecard import (
    Counted,
    CounterName,
    MeasureStatus,
    ScoringWindow,
)
from app.modules.agent.domain.scorecard_work import UnitTable
from app.modules.agent.services.scorecard_preview import (
    MAX_PREVIEW_MEASURES,
    NO_ROWS,
    preview_draft,
    preview_saved,
    rows_behind,
)
from app.modules.agent.tests.unit.scorecard_fakes import FakeScorecardSource

pytestmark = pytest.mark.unit

TODAY = date(2026, 10, 12)

CALLBACKS = UnitTable(
    name="callbacks",
    primary_key="id",
    columns={"id": "UUID", "customer": "TEXT", "due": "DATE", "done": "BOOLEAN"},
)


def _row(key: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "key": key,
        "measure": key.replace("_", " "),
        "counter": "work",
        "shape": "share",
        "unit_table": "callbacks",
        "time_column": "due",
        "test": "coalesce(done, false)",
        "aim": "higher",
        "target": 0.9,
        "target_label": "9 in 10",
        "is_on": True,
        "position": 1,
    }
    row.update(overrides)
    return row


def _source(*rows: dict[str, object], **fields: object) -> FakeScorecardSource:
    return FakeScorecardSource(
        rows=list(rows),
        tables={"callbacks": CALLBACKS},
        **fields,  # type: ignore[arg-type]  # test builder over the fake's fields
    )


# --- Which weeks, which measures -----------------------------------------------


@pytest.mark.asyncio
async def test_preview_counts_each_week_oldest_first_from_its_own_statement():
    source = _source(
        _row("callbacks_on_time"),
        answers={
            "2026-09-14": [{"counted": 9, "total": 10}],
            "2026-09-21": [{"counted": 4, "total": 10}],
            "2026-09-28": [{"counted": 0, "total": 0}],
            "2026-10-05": [{"counted": 2, "total": 3}],
        },
    )

    preview = await preview_saved(source, keys=None, weeks=4, end=None, today=TODAY)

    assert [(w.start, w.end) for w in preview.windows] == [
        (date(2026, 9, 14), date(2026, 9, 21)),
        (date(2026, 9, 21), date(2026, 9, 28)),
        (date(2026, 9, 28), date(2026, 10, 5)),
        (date(2026, 10, 5), date(2026, 10, 12)),
    ]
    (history,) = preview.measures
    assert [week.shown for week in history.weeks] == [
        "9 of 10",
        "4 of 10",
        "nothing to count",
        "too few to judge (3)",
    ]
    assert [week.met for week in history.weeks] == [True, False, None, None]
    # One statement per week, each bounded by its own window.
    assert len(source.queries) == 4
    assert "'2026-09-14'::date" in source.queries[0]
    assert "'2026-10-12'::date" in source.queries[-1]


@pytest.mark.asyncio
async def test_preview_without_keys_counts_what_is_on_and_what_is_proposed():
    source = _source(
        _row("on", position=2),
        _row("proposed", position=1, is_on=False, proposed_from="callbacks matter"),
        _row("off", position=0, is_on=False),
        _row("standing_work", counter="standing_work", position=3, target=1),
        platform={CounterName.STANDING_WORK: Counted(counted=1, total=1)},
    )

    preview = await preview_saved(source, keys=None, weeks=2, end=None, today=TODAY)

    assert [m.key for m in preview.measures] == ["proposed", "on", "standing_work"]
    standing = preview.measures[-1]
    assert [week.shown for week in standing.weeks] == ["1 of 1", "1 of 1"]
    assert [counter for counter, _ in source.platform_counts] == [
        CounterName.STANDING_WORK,
        CounterName.STANDING_WORK,
    ]


@pytest.mark.asyncio
async def test_preview_with_keys_counts_those_measures_whatever_their_state():
    source = _source(_row("on"), _row("off", is_on=False))

    preview = await preview_saved(source, keys=["off"], weeks=1, end=None, today=TODAY)

    assert [m.key for m in preview.measures] == ["off"]


@pytest.mark.asyncio
async def test_preview_with_a_key_no_measure_has_is_not_found_and_runs_nothing():
    source = _source(_row("on"))

    with pytest.raises(ScorecardMeasureNotFoundError, match="`nope`"):
        await preview_saved(source, keys=["on", "nope"], weeks=4, end=None, today=TODAY)

    assert source.queries == []


@pytest.mark.asyncio
async def test_preview_of_a_pod_without_a_scorecard_is_not_found():
    with pytest.raises(ScorecardNotFoundError):
        await preview_saved(
            FakeScorecardSource(rows=None), keys=None, weeks=4, end=None, today=TODAY
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("weeks", [0, 9])
async def test_preview_refuses_a_number_of_weeks_outside_its_bound(weeks):
    with pytest.raises(ScorecardRefusedError, match="`weeks` must be between 1 and 8"):
        await preview_saved(_source(), keys=None, weeks=weeks, end=None, today=TODAY)


@pytest.mark.asyncio
async def test_preview_refuses_weeks_that_have_not_ended():
    with pytest.raises(ScorecardRefusedError, match="after today"):
        await preview_saved(
            _source(),
            keys=None,
            weeks=4,
            end=TODAY + timedelta(days=1),
            today=TODAY,
        )


@pytest.mark.asyncio
async def test_preview_counts_at_most_its_cap_and_says_how_many_it_left():
    rows = [_row(f"m{n:02}", position=n) for n in range(MAX_PREVIEW_MEASURES + 3)]
    source = _source(*rows)

    preview = await preview_saved(source, keys=None, weeks=1, end=None, today=TODAY)

    assert len(preview.measures) == MAX_PREVIEW_MEASURES
    assert preview.measures_skipped == 3


# --- A measure that cannot run --------------------------------------------------


@pytest.mark.asyncio
async def test_a_saved_measure_naming_a_missing_table_fails_each_week_unrun():
    source = _source(_row("lost", unit_table="tickets"))

    preview = await preview_saved(source, keys=None, weeks=2, end=None, today=TODAY)

    weeks = preview.measures[0].weeks
    assert [week.status for week in weeks] == [MeasureStatus.FAILED] * 2
    assert "this pod has no table by that name" in (weeks[0].reason or "")
    assert source.queries == []


@pytest.mark.asyncio
async def test_a_saved_measure_on_a_table_the_caller_cannot_read_fails_with_why():
    source = _source(_row("private"), forbidden={"callbacks"})

    preview = await preview_saved(source, keys=None, weeks=1, end=None, today=TODAY)

    assert (
        preview.measures[0].weeks[0].reason == "Not allowed to read table 'callbacks'"
    )
    assert source.queries == []


@pytest.mark.asyncio
async def test_a_refused_statement_fails_the_measure_not_the_preview():
    source = _source(
        _row("broken"),
        _row("standing_work", counter="standing_work", position=2),
        refusal=DomainError('column "done" does not exist', status_code=400),
    )

    preview = await preview_saved(source, keys=None, weeks=1, end=None, today=TODAY)

    broken, standing = preview.measures
    assert broken.weeks[0].status is MeasureStatus.FAILED
    assert broken.weeks[0].reason == 'column "done" does not exist'
    assert standing.weeks[0].status is MeasureStatus.NOTHING_TO_COUNT


# --- A draft ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_draft_is_counted_over_each_week_without_being_saved():
    source = _source(answers={"2026-10-05": [{"counted": 5, "total": 5}]})
    draft = _row("draft", is_on=None)

    preview = await preview_draft(source, draft, weeks=4, end=None, today=TODAY)

    (history,) = preview.measures
    assert history.key == "draft"
    assert [week.shown for week in history.weeks][-1] == "5 of 5"
    assert len(source.queries) == 4
    assert source.rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"test": "done; drop table callbacks"}, "may not contain `;`"),
        ({"time_column": "deadline"}, "not a column of `callbacks`"),
        ({"unit_table": "Callbacks"}, "lower-case table or column name"),
        ({"unit_table": "tickets"}, "no table by that name"),
        ({"counter": "intercom"}, "`counter` must be one of"),
        ({"aim": None}, "`aim` must be"),
        ({"shape": "mean"}, "`shape` must be"),
        ({"counter": "sql", "query": "SELECT 1; SELECT 2"}, "single statement"),
        ({"shape": "median", "value": None}, "A median needs a `value`"),
    ],
)
async def test_a_draft_that_cannot_run_is_refused_with_why_before_anything_runs(
    overrides, reason
):
    source = _source()

    with pytest.raises(ScorecardRefusedError) as refused:
        await preview_draft(
            source, _row("draft", **overrides), weeks=4, end=None, today=TODAY
        )

    assert reason in refused.value.message
    assert source.queries == []
    assert source.platform_counts == []


@pytest.mark.asyncio
async def test_a_draft_platform_counter_is_previewed_like_a_saved_one():
    source = _source(platform={CounterName.OPEN_QUESTIONS: Counted(2, 9)})
    draft = _row("open_questions", counter="open_questions", aim="lower", target=0)
    draft["shape"] = None

    preview = await preview_draft(source, draft, weeks=2, end=None, today=TODAY)

    assert [week.shown for week in preview.measures[0].weeks] == ["2", "2"]
    assert source.queries == []


# --- The rows behind a number --------------------------------------------------------


@pytest.mark.asyncio
async def test_rows_behind_a_work_measure_are_its_units_for_the_week():
    source = _source(
        _row("callbacks_on_time"),
        default=[
            {
                "id": "a",
                "label": "Ana",
                "link": None,
                "at": date(2026, 10, 6),
                "passed": False,
            },
            {
                "id": "b",
                "label": "Bo",
                "link": None,
                "at": date(2026, 10, 7),
                "passed": True,
            },
            {
                "id": "c",
                "label": "Cy",
                "link": None,
                "at": date(2026, 10, 8),
                "passed": True,
            },
        ],
    )

    found = await rows_behind(
        source, "callbacks_on_time", end=None, limit=2, today=TODAY
    )

    assert found.window == ScoringWindow(start=date(2026, 10, 5), end=TODAY)
    assert [(row.id, row.label, row.at, row.passed) for row in found.rows] == [
        ("a", "Ana", "2026-10-06", False),
        ("b", "Bo", "2026-10-07", True),
    ]
    # A third row came back past the limit: the list was cut, and says so.
    assert found.truncated is True
    assert source.queries[0].endswith("LIMIT 3")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("counter", "why"),
    [
        ("sql", "counted by its own SQL"),
        ("approvals", "the platform counts it"),
        ("standing_work", "the platform counts it"),
        ("intercom", "nothing counts it yet"),
    ],
)
async def test_a_measure_that_is_not_work_has_no_rows_to_show(counter, why):
    source = _source(_row("other", counter=counter, query="SELECT 1"))

    with pytest.raises(ScorecardRefusedError) as refused:
        await rows_behind(source, "other", end=None, limit=50, today=TODAY)

    assert refused.value.code == NO_ROWS
    assert refused.value.status_code == 422
    assert "has no rows to show" in refused.value.message
    assert why in refused.value.message
    assert source.queries == []


@pytest.mark.asyncio
async def test_rows_behind_a_key_no_measure_has_is_not_found():
    with pytest.raises(ScorecardMeasureNotFoundError):
        await rows_behind(_source(_row("on")), "nope", end=None, limit=50, today=TODAY)


@pytest.mark.asyncio
async def test_rows_behind_a_measure_that_cannot_run_say_why():
    source = _source(_row("broken", time_column="deadline"))

    with pytest.raises(ScorecardRefusedError, match="not a column of `callbacks`"):
        await rows_behind(source, "broken", end=None, limit=50, today=TODAY)

    assert source.queries == []
