"""The scorecard's arithmetic, window and SQL rules, pinned without a database.

Each test is a rule the weekly review depends on and a reader might reasonably
argue with: what a week is, what "met" means for a measure that aims low, what
a week with nothing in it scores, what too few rows to judge is, and which SQL
a scorecard row may hand the datastore. The work measure's guards and SQL are
in ``test_scorecard_work``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.scorecard import (
    Aim,
    CountFailed,
    Counted,
    Measure,
    MeasureProblem,
    MeasureStatus,
    NotCounted,
    QueryRejected,
    ScorecardInputError,
    ScoringWindow,
    Shape,
    WorkFields,
    count_from_rows,
    fill_query,
    history_windows,
    is_on,
    is_proposal,
    measure_from_row,
    read_measures,
    scoring_window,
    select_measures,
)
from app.modules.agent.domain.scorecard_scoring import (
    MIN_UNITS_TO_JUDGE,
    evaluate,
    format_median,
    format_number,
    problem_result,
    shown_for,
    week_row,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import ScoreWeekRequest
from app.modules.agent.tools.pod.scorecard import score_week

pytestmark = pytest.mark.unit

TODAY = date(2026, 10, 12)
WINDOW = ScoringWindow(start=date(2026, 10, 5), end=date(2026, 10, 12))


def _measure(**overrides: object) -> Measure:
    """A measure; its shape follows its aim, as a row written without one does."""
    fields: dict[str, object] = {
        "key": "approved",
        "measure": "Work you approve when it asks",
        "counter": "approvals",
        "query": None,
        "aim": Aim.HIGHER,
        "target": 0.9,
        "target_label": "9 in 10",
        "position": 1,
    }
    fields.update(overrides)
    fields.setdefault(
        "shape", Shape.COUNT if fields["aim"] is Aim.LOWER else Shape.SHARE
    )
    return Measure(**fields)  # type: ignore[arg-type]  # test builder over a frozen dataclass


def _row(**overrides: object) -> dict[str, object]:
    """A scorecard row shaped like the template CSVs."""
    row: dict[str, object] = {
        "key": "approved",
        "measure": "Work you approve when it asks",
        "kind": "verdict",
        "counter": "approvals",
        "query": None,
        "aim": "higher",
        "target": 0.9,
        "target_label": "9 in 10",
        "counted_from": "Your approvals",
        "is_on": True,
        "note": None,
        "position": 1,
    }
    row.update(overrides)
    return row


# --- The window -------------------------------------------------------------


def test_scoring_window_defaults_to_the_seven_whole_days_before_today():
    window = scoring_window(None, today=TODAY)

    assert window == WINDOW
    assert window.start_at == datetime(2026, 10, 5, tzinfo=timezone.utc)
    assert window.end_at == datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert window.end_at - window.start_at == timedelta(days=7)


def test_scoring_window_counts_an_earlier_week_when_given_its_end():
    window = scoring_window(date(2026, 9, 28), today=TODAY)

    assert (window.start, window.end) == (date(2026, 9, 21), date(2026, 9, 28))


def test_scoring_window_refuses_an_end_after_today():
    with pytest.raises(ScorecardInputError, match="after today"):
        scoring_window(TODAY + timedelta(days=1), today=TODAY)


def test_history_windows_are_consecutive_weeks_oldest_first_ending_at_end():
    windows = history_windows(None, weeks=3, today=TODAY)

    assert [(w.start, w.end) for w in windows] == [
        (date(2026, 9, 21), date(2026, 9, 28)),
        (date(2026, 9, 28), date(2026, 10, 5)),
        (date(2026, 10, 5), date(2026, 10, 12)),
    ]
    # The last is exactly the week `score_week` would count today.
    assert windows[-1] == scoring_window(None, today=TODAY)


def test_history_windows_refuse_an_end_after_today():
    with pytest.raises(ScorecardInputError, match="after today"):
        history_windows(TODAY + timedelta(days=1), weeks=4, today=TODAY)


# --- Reading rows -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE ", True),
        ("t", True),
        ("false", False),
        ("", False),
        (None, False),
        (1, False),
    ],
)
def test_is_on_reads_booleans_and_their_spellings_and_nothing_else(value, expected):
    assert is_on({"is_on": value}) is expected


def test_read_measures_skips_off_rows_and_orders_by_position_with_unplaced_last():
    rows = [
        _row(key="third", position=3),
        _row(key="off", position=0, is_on=False),
        _row(key="unplaced", position=None),
        _row(key="first", position=1),
        _row(key="missing_flag", position=2, is_on=None),
    ]

    keys = [measure.key for measure in read_measures(rows)]

    assert keys == ["first", "third", "unplaced"]


def test_measure_from_row_parses_a_template_row():
    measure = measure_from_row(
        _row(counter=" SQL ", query="SELECT 1", aim="Lower", target="0", position="4")
    )

    assert measure == Measure(
        key="approved",
        measure="Work you approve when it asks",
        counter="sql",
        query="SELECT 1",
        aim=Aim.LOWER,
        target=0.0,
        target_label="9 in 10",
        position=4,
        # Written before shapes: a measure aiming low was always a count.
        shape=Shape.COUNT,
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"key": ""}, "no `key`"),
        ({"aim": "sideways"}, "`aim` must be"),
        ({"aim": None}, "`aim` must be"),
        ({"target": "most of them"}, "`target` must be a number"),
        ({"target": None}, "`target` must be a number"),
        # `True` is an int to Python and not a target to anyone.
        ({"target": True}, "`target` must be a number"),
        ({"shape": "average"}, "`shape` must be"),
    ],
)
def test_measure_from_row_names_what_stops_a_row_being_scored(overrides, reason):
    problem = measure_from_row(_row(**overrides))

    assert isinstance(problem, MeasureProblem)
    assert reason in problem.reason


@pytest.mark.parametrize(
    ("aim", "shape", "expected"),
    [
        ("higher", None, Shape.SHARE),
        ("lower", None, Shape.COUNT),
        ("lower", "share", Shape.SHARE),
        ("higher", " Median ", Shape.MEDIAN),
        ("higher", "count", Shape.COUNT),
    ],
)
def test_measure_from_row_reads_a_shape_or_takes_it_from_the_aim(aim, shape, expected):
    measure = measure_from_row(_row(aim=aim, shape=shape))

    assert isinstance(measure, Measure)
    assert measure.shape is expected


def test_measure_from_row_reads_a_work_measure_as_written():
    measure = measure_from_row(
        _row(
            counter="Work",
            shape="median",
            unit_table=" conversations ",
            time_column="started_at",
            test="",
            value="extract(epoch from first_reply_at - started_at) / 60",
            value_unit="minutes",
            label_column=None,
            link_column="link",
        )
    )

    assert isinstance(measure, Measure)
    assert measure.counter == "work"
    assert measure.value_unit == "minutes"
    assert measure.work == WorkFields(
        unit_table="conversations",
        time_column="started_at",
        test=None,
        value="extract(epoch from first_reply_at - started_at) / 60",
        label_column=None,
        link_column="link",
    )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"proposed_from": "callbacks should not slip", "is_on": False}, True),
        # Kept: a person turned it on, whatever it was drafted from.
        ({"proposed_from": "callbacks should not slip", "is_on": True}, False),
        # Turned off, never proposed.
        ({"proposed_from": None, "is_on": False}, False),
        ({"proposed_from": "  ", "is_on": False}, False),
    ],
)
def test_is_proposal_is_a_drafted_row_nobody_has_kept(row, expected):
    assert is_proposal(row) is expected


def test_read_measures_leaves_proposals_out_unless_asked_for_them():
    rows = [
        _row(key="on", position=1),
        _row(key="proposed", position=2, is_on=False, proposed_from="the words"),
        _row(key="off", position=3, is_on=False),
    ]

    assert [m.key for m in read_measures(rows)] == ["on"]
    assert [m.key for m in read_measures(rows, proposals=True)] == ["on", "proposed"]


def test_select_measures_finds_keys_whatever_their_state_and_names_the_missing():
    rows = [
        _row(key="third", position=3, is_on=False),
        _row(key="first", position=1),
        _row(key="broken", position=2, aim="sideways"),
    ]

    found, missing = select_measures(rows, ["third", "first", "nope", "broken"])

    assert [m.key for m in found] == ["first", "broken", "third"]
    assert isinstance(found[1], MeasureProblem)
    assert missing == ["nope"]


def test_a_row_that_cannot_be_scored_fails_without_a_number():
    problem = measure_from_row(_row(aim="sideways"))
    assert isinstance(problem, MeasureProblem)

    result = problem_result(problem)

    assert result.status is MeasureStatus.FAILED
    assert result.shown == "could not count"
    assert (result.counted, result.total, result.value, result.met) == (
        None,
        None,
        None,
        None,
    )
    assert result.reason is not None and "`aim`" in result.reason


# --- Placeholders -------------------------------------------------------------


def test_fill_query_substitutes_start_and_end_as_iso_dates():
    query = (
        "SELECT count(*) FILTER (WHERE done) AS counted, count(*) AS total "
        "FROM callbacks WHERE due >= '{start}'::date AND due < '{end}'::date"
    )

    filled = fill_query(query, WINDOW)

    assert filled == (
        "SELECT count(*) FILTER (WHERE done) AS counted, count(*) AS total "
        "FROM callbacks WHERE due >= '2026-10-05'::date AND due < '2026-10-12'::date"
    )


def test_fill_query_takes_end_alone_for_what_stood_as_the_week_closed():
    query = "SELECT count(*) AS counted, 0 AS total FROM t WHERE due < '{end}'::date"

    assert fill_query(query, WINDOW).endswith("due < '2026-10-12'::date")


@pytest.mark.parametrize(
    "query",
    [
        # Every impression ever, the same in every week: how a "reach per
        # month" goal was once written, as a share of a fixed total.
        "SELECT coalesce(sum(impressions), 0) AS counted, 100000 AS total FROM posts",
        # From the week's start onward lets every later week into this one.
        "SELECT count(*) AS counted, count(*) AS total FROM t WHERE at >= '{start}'",
    ],
)
def test_fill_query_refuses_a_query_that_does_not_name_the_week(query):
    with pytest.raises(QueryRejected, match="must use `\\{end\\}`"):
        fill_query(query, WINDOW)


def test_fill_query_drops_one_trailing_semicolon():
    assert fill_query(
        "SELECT 1 AS counted, 1 AS total WHERE '{end}' > '' ;  ", WINDOW
    ) == ("SELECT 1 AS counted, 1 AS total WHERE '2026-10-12' > ''")


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1 AS counted, 1 AS total; DROP TABLE commitments",
        "SELECT 1 AS counted, 1 AS total; SELECT 2;",
        # A semicolon inside a literal is refused too: telling the two apart is
        # parsing, and this check is meant to be too simple to get wrong.
        "SELECT count(*) AS counted, count(*) AS total FROM t WHERE note = 'a;b'",
    ],
)
def test_fill_query_refuses_a_second_statement(query):
    with pytest.raises(QueryRejected, match="single statement"):
        fill_query(query, WINDOW)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1 AS counted, 1 AS total WHERE '{start_date}' = ''",
        "SELECT 1 AS counted, 1 AS total WHERE '{key}' = ''",
        # A format string would read attributes off the value it is handed.
        "SELECT 1 AS counted, 1 AS total WHERE '{end.__class__}' = ''",
        "SELECT 1 AS counted, 1 AS total WHERE '{{start}}' = ''",
        "SELECT 1 AS counted, 1 AS total WHERE '{}' = ''",
        "SELECT 1 AS counted, 1 AS total WHERE '}' = ''",
    ],
)
def test_fill_query_refuses_any_brace_but_the_two_placeholders(query):
    with pytest.raises(QueryRejected, match="no other braces"):
        fill_query(query, WINDOW)


@pytest.mark.parametrize("query", [None, "", "   ", ";"])
def test_fill_query_refuses_a_measure_with_no_query(query):
    with pytest.raises(QueryRejected):
        fill_query(query, WINDOW)


# --- Reading a measure's SQL result -------------------------------------------


def test_count_from_rows_reads_counted_and_total():
    assert count_from_rows([{"counted": 31, "total": Decimal("40")}]) == Counted(
        counted=31.0, total=40.0
    )


def test_count_from_rows_reads_an_aggregate_over_nothing_as_zero():
    assert count_from_rows([{"counted": None, "total": 0}]) == Counted(0.0, 0.0)


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([], "exactly one row; it returned 0"),
        ([{"counted": 1, "total": 1}] * 2, "exactly one row; it returned 2"),
        ([{"total": 1}], "a `counted` column"),
        ([{"counted": 1}], "a `total` column"),
        ([{"counted": "many", "total": 1}], "`counted` must be a number"),
        ([{"counted": 1, "total": True}], "`total` must be a number"),
    ],
)
def test_count_from_rows_fails_a_result_it_cannot_read(rows, reason):
    outcome = count_from_rows(rows)

    assert isinstance(outcome, CountFailed)
    assert reason in outcome.reason


# --- Scoring ------------------------------------------------------------------


def test_a_share_is_counted_over_total_and_shown_as_its_two_counts():
    result = evaluate(_measure(), Counted(counted=31, total=40))

    assert result.status is MeasureStatus.COUNTED
    assert result.value == pytest.approx(0.775)
    assert result.met is False
    assert result.shown == "31 of 40"
    assert (result.counted, result.total) == (31, 40)


def test_a_share_exactly_on_target_is_met():
    result = evaluate(_measure(target=0.9), Counted(counted=27, total=30))

    assert result.met is True
    assert result.shown == "27 of 30"


def test_a_share_of_nothing_has_no_value_and_no_verdict():
    result = evaluate(_measure(target=1), Counted(counted=0, total=0))

    assert result.status is MeasureStatus.NOTHING_TO_COUNT
    assert result.value is None
    assert result.met is None
    assert result.shown == "nothing to count"
    # What was found is still recorded: nothing, out of nothing.
    assert (result.counted, result.total) == (0, 0)


def test_a_count_aiming_low_is_the_count_itself_and_none_when_zero():
    result = evaluate(_measure(aim=Aim.LOWER, target=0), Counted(counted=0, total=12))

    assert result.status is MeasureStatus.COUNTED
    assert result.value == 0
    assert result.met is True
    assert result.shown == "none"


def test_a_count_aiming_low_above_target_is_not_met_and_ignores_total():
    result = evaluate(_measure(aim=Aim.LOWER, target=0), Counted(counted=2, total=0))

    assert result.status is MeasureStatus.COUNTED
    assert result.value == 2
    assert result.met is False
    assert result.shown == "2"


def test_a_count_aiming_low_at_target_is_met():
    result = evaluate(_measure(aim=Aim.LOWER, target=3), Counted(counted=3, total=9))

    assert result.met is True


def test_a_measure_nobody_counts_yet_has_no_number():
    result = evaluate(_measure(counter="intercom_first_reply"), NotCounted())

    assert result.status is MeasureStatus.NOT_COUNTED
    assert result.shown == "not counted"
    assert (result.counted, result.total, result.value, result.met) == (
        None,
        None,
        None,
        None,
    )


def test_a_failed_count_keeps_its_reason_and_has_no_number():
    result = evaluate(_measure(), CountFailed("Table 'callbacks' not found"))

    assert result.status is MeasureStatus.FAILED
    assert result.shown == "could not count"
    assert result.reason == "Table 'callbacks' not found"
    assert result.value is None and result.met is None


def test_a_share_of_fewer_rows_than_the_minimum_is_counted_but_not_judged():
    result = evaluate(_measure(target=0.9), Counted(counted=3, total=4))

    assert MIN_UNITS_TO_JUDGE == 5
    assert result.status is MeasureStatus.TOO_FEW
    assert result.shown == "too few to judge (4)"
    assert result.met is None
    # What was found is kept, so a chart can still draw the week.
    assert (result.counted, result.total) == (3, 4)
    assert result.value == pytest.approx(0.75)


def test_a_share_of_exactly_the_minimum_is_judged():
    result = evaluate(_measure(target=0.9), Counted(counted=4, total=5))

    assert result.status is MeasureStatus.COUNTED
    assert result.met is False
    assert result.shown == "4 of 5"


def test_a_share_aiming_for_every_one_is_judged_however_few():
    """One miss of two already misses "every one"; there is nothing to wait for."""
    missed = evaluate(_measure(target=1), Counted(counted=1, total=2))
    met = evaluate(_measure(target=1), Counted(counted=1, total=1))

    assert (missed.status, missed.met, missed.shown) == (
        MeasureStatus.COUNTED,
        False,
        "1 of 2",
    )
    assert (met.status, met.met) == (MeasureStatus.COUNTED, True)


def test_a_share_aiming_lower_is_still_a_share_met_at_or_under_target():
    measure = _measure(aim=Aim.LOWER, shape=Shape.SHARE, target=0.2)

    result = evaluate(measure, Counted(counted=2, total=10))

    assert result.value == pytest.approx(0.2)
    assert result.met is True
    assert result.shown == "2 of 10"


def test_a_count_is_shown_in_its_unit_and_singular_for_exactly_one():
    measure = _measure(aim=Aim.LOWER, target=0, value_unit="views")

    assert evaluate(measure, Counted(counted=2, total=9)).shown == "2 views"
    assert evaluate(measure, Counted(counted=1, total=9)).shown == "1 view"
    assert evaluate(measure, Counted(counted=0, total=9)).shown == "none"


def test_a_count_aiming_higher_is_met_at_or_above_its_target():
    measure = _measure(aim=Aim.HIGHER, shape=Shape.COUNT, target=3)

    met = evaluate(measure, Counted(counted=3, total=3))
    missed = evaluate(measure, Counted(counted=2, total=2))

    assert (met.value, met.met, met.shown) == (3, True, "3")
    assert (missed.value, missed.met) == (2, False)


def test_a_median_is_its_own_value_shown_in_its_unit():
    measure = _measure(aim=Aim.LOWER, shape=Shape.MEDIAN, target=2, value_unit="days")

    result = evaluate(measure, Counted(counted=1.63, total=12))

    assert result.status is MeasureStatus.COUNTED
    assert result.value == pytest.approx(1.63)
    assert result.met is True
    assert result.shown == "1.6 days"
    assert result.total == 12


def test_a_median_is_never_too_few_to_judge():
    """The minimum is about a share, where one row moves the verdict by a
    fraction; a median of three is still the middle one of three."""
    measure = _measure(aim=Aim.LOWER, shape=Shape.MEDIAN, target=30)

    result = evaluate(measure, Counted(counted=42, total=3))

    assert (result.status, result.met) == (MeasureStatus.COUNTED, False)


def test_a_total_is_its_own_value_shown_in_its_unit():
    measure = _measure(shape=Shape.TOTAL, target=25_000, value_unit="impressions")

    met = evaluate(measure, Counted(counted=31_204, total=6))
    missed = evaluate(measure, Counted(counted=1_250.5, total=2))

    assert (met.status, met.value, met.met) == (MeasureStatus.COUNTED, 31_204, True)
    assert met.shown == "31,204 impressions"
    assert (missed.met, missed.shown) == (False, "1,250.5 impressions")


def test_a_total_of_nothing_is_zero_and_a_verdict():
    """A week with no posts reached nobody. That is a number, unlike a median
    of no rows, and against a target of reaching people it is a miss."""
    measure = _measure(shape=Shape.TOTAL, target=25_000, value_unit="impressions")

    result = evaluate(measure, Counted(counted=0, total=0))

    assert (result.status, result.value, result.met) == (
        MeasureStatus.COUNTED,
        0,
        False,
    )
    assert result.shown == "0 impressions"


def test_a_median_of_nothing_has_no_value_and_no_verdict():
    measure = _measure(aim=Aim.LOWER, shape=Shape.MEDIAN, target=30)

    # A median over no rows comes back NULL, which reads as zero, with a
    # total of zero: the total is what says there was nothing.
    result = evaluate(measure, Counted(counted=0, total=0))

    assert result.status is MeasureStatus.NOTHING_TO_COUNT
    assert (result.value, result.met) == (None, None)
    assert result.shown == "nothing to count"


@pytest.mark.parametrize(
    ("shape", "counted", "total", "unit", "shown"),
    [
        (Shape.SHARE, 31, 40, None, "31 of 40"),
        (Shape.SHARE, 1204, 2000, "views", "1,204 of 2,000"),
        (Shape.COUNT, 0, 4, "days", "none"),
        (Shape.COUNT, 1204, 0, None, "1,204"),
        (Shape.MEDIAN, 31.4, 9, "minutes", "31 minutes"),
        (Shape.MEDIAN, 1.0, 9, "days", "1.0 days"),
        (Shape.MEDIAN, 1234.5, 9, None, "1,235"),
    ],
)
def test_shown_for_writes_each_shape_the_way_a_person_does(
    shape, counted, total, unit, shown
):
    assert shown_for(shape, counted, total, unit=unit) == shown


@pytest.mark.parametrize(
    ("number", "shown"),
    [(0, "0"), (7, "7"), (1204, "1,204"), (2.5, "2.5"), (2.25, "2.25"), (40.0, "40")],
)
def test_format_number_writes_counts_the_way_a_person_does(number, shown):
    assert format_number(number) == shown


@pytest.mark.parametrize(
    ("number", "shown"),
    [
        (1.63, "1.6"),
        (0.04, "0.0"),
        (2.25, "2.3"),  # half up, not to even
        (9.94, "9.9"),
        (9.96, "10"),  # rounds into the whole numbers, so loses its decimal
        (10.5, "11"),
        (31.4, "31"),
        (1234.5, "1,235"),
    ],
)
def test_format_median_keeps_one_decimal_under_ten_and_none_above(number, shown):
    assert format_median(number) == shown


def test_week_row_records_the_week_by_its_end_date():
    result = evaluate(_measure(), Counted(counted=9, total=10))

    assert week_row(WINDOW.end, result) == {
        "week": "2026-10-12",
        "key": "approved",
        "measure": "Work you approve when it asks",
        "counted": 9,
        "total": 10,
        "value": 0.9,
        "shown": "9 of 10",
        "met": True,
        "status": "counted",
        "target_label": "9 in 10",
    }


# --- The tool boundary --------------------------------------------------------


@pytest.mark.asyncio
async def test_score_week_refuses_a_week_that_has_not_ended_before_touching_the_pod():
    """No services are reachable from this context, so a refusal that came any
    later than the window check would fail differently."""
    ctx = SimpleNamespace(
        deps=BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            is_pod_default_agent=True,
        )
    )
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)

    result = await score_week(ctx, ScoreWeekRequest(end=tomorrow))  # type: ignore[arg-type]  # a RunContext stand-in carrying only deps

    assert result["success"] is False
    assert "after today" in str(result["error"])
