"""A teammate's scorecard, scored in code.

The rule this file exists for is *numbers by code, words by the model*. The
weekly review is a model writing about how its week went, and a model asked for
a share will produce one whether or not anything was counted. So every number
the review quotes is computed here, from counts the platform took, and the
model's job is the sentence around it.

A scorecard counts rows of the teammate's work. A measure names a table with
one row per unit of work, the column that places each row in a week, a test
over one row, and a shape: the share of rows that pass, the count that pass,
or the median of a value over them. ``scorecard_work`` turns that into SQL and
``scorecard_scoring`` turns what the SQL found into a verdict.

Everything below is pure -- no database, no clock -- so each rule a reader might
argue with (what a week is, what "met" means for a measure that aims low, which
characters a measure's SQL may contain) is pinned by a unit test rather than by
a run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum

#: The table a teammate is judged by -- one row per measure -- and the table the
#: weekly count is written to, one row per measure per week.
SCORECARD_TABLE = "scorecard"
WEEKS_TABLE = "scorecard_weeks"

WEEK = timedelta(days=7)

#: How long a question may wait on a person before it counts against the week.
#: The measure is "nothing waits on a question for a day", and a day is what it
#: says; a question asked an hour before the week closed is not yet late.
QUESTION_PATIENCE = timedelta(hours=24)


class Aim(StrEnum):
    #: Met at or above the target.
    HIGHER = "higher"
    #: Met at or below the target.
    LOWER = "lower"


class Shape(StrEnum):
    """What a measure's number is."""

    #: Rows that passed the test, out of every row in the week.
    SHARE = "share"
    #: Rows that passed the test, as a number.
    COUNT = "count"
    #: The middle ``value`` over the rows that passed the test.
    MEDIAN = "median"
    #: The ``value`` of the rows that passed the test, added up: reach, views,
    #: revenue -- a number the week's work adds to, not a number of rows.
    TOTAL = "total"


class CounterName(StrEnum):
    """The counters the platform knows how to run.

    Any other name on a row is a measure somebody described before anything
    could count it, and it scores ``not_counted`` rather than failing.
    """

    APPROVALS = "approvals"
    STANDING_WORK = "standing_work"
    OPEN_QUESTIONS = "open_questions"
    SQL = "sql"
    #: Rows of a table of the teammate's work, described by the row's
    #: ``unit_table``, ``time_column``, ``test`` and ``value``.
    WORK = "work"


#: The counters whose numbers the platform takes from its own records, not from
#: a table of the pod's. None of them has rows a person could open.
PLATFORM_COUNTERS = frozenset(
    {CounterName.APPROVALS, CounterName.STANDING_WORK, CounterName.OPEN_QUESTIONS}
)


class MeasureStatus(StrEnum):
    COUNTED = "counted"
    #: A share or a median with nothing to take it of: no value, no verdict.
    NOTHING_TO_COUNT = "nothing_to_count"
    #: A share of so few rows that one row decides it; counted, not judged.
    TOO_FEW = "too_few"
    #: Nothing on the platform counts this measure yet.
    NOT_COUNTED = "not_counted"
    #: The row or its query could not be scored as written.
    FAILED = "failed"


class ScorecardInputError(ValueError):
    """An argument the scorecard refuses, with a reason a model can repeat."""


class QueryRejected(ValueError):
    """A measure's SQL, or the parts it is built from, refused before it runs."""


@dataclass(frozen=True, slots=True)
class ScoringWindow:
    """``[start, end)`` in whole UTC days."""

    start: date
    end: date

    @property
    def start_at(self) -> datetime:
        return _midnight(self.start)

    @property
    def end_at(self) -> datetime:
        return _midnight(self.end)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def scoring_window(end: date | None, *, today: date) -> ScoringWindow:
    """The seven whole days before ``end``, which defaults to today.

    Days rather than a rolling 168 hours, because a measure's own SQL compares
    against ``'{end}'::date``, and the counters run here have to agree with it
    on where the week begins. UTC days, so that every counter -- the ledger's
    timestamps, the conversation tables', the SQL's dates -- reads one calendar.

    Defaulting to today rather than now keeps the run doing the counting out of
    its own window. The weekly review is itself a scheduled run, due the moment
    it starts; a window ending now would judge it while it is still running.

    An ``end`` after today is refused. That week has not finished, and the week
    is recorded under its end date: a part-week written there now would later
    be indistinguishable from the real one.
    """
    closing = end or today
    if closing > today:
        raise ScorecardInputError(
            f"`end` cannot be after today ({today.isoformat()}): that week has "
            "not finished yet."
        )
    return ScoringWindow(start=closing - WEEK, end=closing)


def history_windows(
    end: date | None, *, weeks: int, today: date
) -> list[ScoringWindow]:
    """``weeks`` consecutive scoring windows, oldest first, the last ending at
    ``end`` -- the weeks ``score_week`` would have counted, one after another."""
    last = scoring_window(end, today=today)
    return [
        ScoringWindow(start=last.start - WEEK * back, end=last.end - WEEK * back)
        for back in reversed(range(weeks))
    ]


# --- Reading the scorecard --------------------------------------------------

ScorecardRow = Mapping[str, object]

_TRUE_WORDS = frozenset({"true", "t", "yes", "y", "1"})


@dataclass(frozen=True, slots=True)
class ScorecardPage:
    """The scorecard's rows as read, and how many it has in all."""

    rows: Sequence[ScorecardRow]
    total: int


@dataclass(frozen=True, slots=True)
class WorkFields:
    """How a ``work`` measure says what to count, as written on its row.

    Unchecked text: ``scorecard_work`` decides whether it may run, against the
    table it names.
    """

    unit_table: str | None = None
    time_column: str | None = None
    test: str | None = None
    value: str | None = None
    label_column: str | None = None
    link_column: str | None = None


@dataclass(frozen=True, slots=True)
class Measure:
    key: str
    measure: str
    counter: str
    query: str | None
    aim: Aim
    target: float
    target_label: str
    position: int | None
    shape: Shape
    work: WorkFields = WorkFields()
    #: The unit a count or a median is said in: "days", "views".
    value_unit: str | None = None


@dataclass(frozen=True, slots=True)
class MeasureProblem:
    """A row that cannot be scored as written, and why."""

    key: str
    measure: str
    target_label: str
    position: int | None
    reason: str


def is_on(row: ScorecardRow) -> bool:
    """Whether a row is switched on. Anything unreadable is off.

    Rows arrive from a typed BOOLEAN column, but a scorecard is also imported
    from CSV and edited by hand, so the spelled-out forms are honoured too.
    """
    value = row.get("is_on")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_WORDS
    return False


def is_proposal(row: ScorecardRow) -> bool:
    """A row the teammate drafted from somebody's words that nobody has kept.

    Off until a person keeps it, so the weekly count never scores a measure
    nobody agreed to; ``proposed_from`` is what tells it from a row somebody
    turned off.
    """
    return bool(_text(row, "proposed_from")) and not is_on(row)


def _text(row: ScorecardRow, name: str) -> str:
    value = row.get(name)
    return "" if value is None else str(value).strip()


def _optional(row: ScorecardRow, name: str) -> str | None:
    return _text(row, name) or None


def _number(value: object) -> float | None:
    """A real number, or ``None``. ``True`` is not one, whatever Python says."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _position(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _aim(row: ScorecardRow) -> Aim | None:
    text = _text(row, "aim").lower()
    return Aim(text) if text in {aim.value for aim in Aim} else None


def _shape(row: ScorecardRow, aim: Aim | None) -> Shape | None:
    """The row's shape; a row written before shapes existed reads as its aim.

    Every such row was a share aiming higher or a count aiming lower, so that
    is what each one keeps meaning.
    """
    text = _text(row, "shape").lower()
    if text:
        return Shape(text) if text in {shape.value for shape in Shape} else None
    return Shape.COUNT if aim is Aim.LOWER else Shape.SHARE


def _why_unscorable(key: str, aim: Aim | None, target: float | None) -> str:
    if not key:
        return "The row has no `key`."
    if aim is None:
        return "`aim` must be 'higher' or 'lower'."
    if target is None:
        return "`target` must be a number."
    return "`shape` must be 'share', 'count', 'median' or 'total'."


def _work_fields(row: ScorecardRow) -> WorkFields:
    return WorkFields(
        unit_table=_optional(row, "unit_table"),
        time_column=_optional(row, "time_column"),
        test=_optional(row, "test"),
        value=_optional(row, "value"),
        label_column=_optional(row, "label_column"),
        link_column=_optional(row, "link_column"),
    )


def measure_from_row(row: ScorecardRow) -> Measure | MeasureProblem:
    """Parse one scorecard row, or say what stops it being scored."""
    key = _text(row, "key")
    name = _text(row, "measure") or key
    label = _text(row, "target_label")
    position = _position(row.get("position"))
    aim = _aim(row)
    target = _number(row.get("target"))
    shape = _shape(row, aim)
    if not key or aim is None or target is None or shape is None:
        return MeasureProblem(
            key=key,
            measure=name,
            target_label=label,
            position=position,
            reason=_why_unscorable(key, aim, target),
        )
    return Measure(
        key=key,
        measure=name,
        counter=_text(row, "counter").lower(),
        query=_optional(row, "query"),
        aim=aim,
        target=target,
        target_label=label,
        position=position,
        shape=shape,
        work=_work_fields(row),
        value_unit=_optional(row, "value_unit"),
    )


def in_scorecard_order(
    measures: Iterable[Measure | MeasureProblem],
) -> list[Measure | MeasureProblem]:
    """The order the scorecard lists its measures in.

    A row with no position goes last rather than first: a measure somebody
    added without placing it should not displace the ones they did place.
    """
    return sorted(
        measures,
        key=lambda measure: (
            measure.position is None,
            measure.position or 0,
            measure.key,
        ),
    )


def read_measures(
    rows: Iterable[ScorecardRow], *, proposals: bool = False
) -> list[Measure | MeasureProblem]:
    """The rows that are on -- and, with ``proposals``, the drafts nobody has
    kept yet -- in the order the scorecard lists them."""
    chosen = (row for row in rows if is_on(row) or (proposals and is_proposal(row)))
    return in_scorecard_order(measure_from_row(row) for row in chosen)


def select_measures(
    rows: Iterable[ScorecardRow], keys: Sequence[str]
) -> tuple[list[Measure | MeasureProblem], list[str]]:
    """The rows with these keys, on or off, and the keys no row has."""
    wanted = dict.fromkeys(key.strip() for key in keys)
    found = [measure_from_row(row) for row in rows if _text(row, "key") in wanted]
    seen = {measure.key for measure in found}
    return in_scorecard_order(found), [key for key in wanted if key not in seen]


# --- Counting -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Counted:
    """What a counter found: for a median, ``counted`` is the median itself and
    ``total`` how many values it was taken over."""

    counted: float
    total: float


@dataclass(frozen=True, slots=True)
class NotCounted:
    """Nothing on the platform counts this measure yet."""


@dataclass(frozen=True, slots=True)
class CountFailed:
    reason: str


CountOutcome = Counted | NotCounted | CountFailed

#: The only substitutions a measure's SQL gets. Literal tokens, never a format
#: string; see :func:`fill_query`.
START_PLACEHOLDER = "{start}"
END_PLACEHOLDER = "{end}"


def fill_query(query: str | None, window: ScoringWindow) -> str:
    """The measure's SQL with ``{start}`` and ``{end}`` replaced by ISO dates.

    Those two literal tokens and nothing else. The query is text somebody wrote
    into a table, and the one thing it must never become is a template: no
    format-string expansion, which reads attributes off whatever it is handed,
    and nothing from the row or anywhere else spliced in. Any other brace is
    refused rather than passed through, so a query written for a richer
    template language fails here, loudly, instead of running with its braces
    read as SQL.

    It must use ``{end}``. A query that names no week counts the same thing
    for every week -- every impression ever, every row ever -- so four weeks of
    bars would be one number drawn four times, and a missed week could never
    show. ``{start}`` and ``{end}`` together count what the week did;
    ``{end}`` alone is a snapshot as the week closed, such as what was still
    open. ``{start}`` alone would let later weeks into an earlier one.

    One statement only. The datastore's parser refuses a second statement too;
    this refuses it before the query leaves this module, so a scorecard row is
    never what hands that parser a script to rule on. A semicolon anywhere but
    the very end is refused, string literals included: telling a literal from a
    separator is parsing, and this check is meant to be too simple to get wrong.
    """
    body = (query or "").strip().removesuffix(";").rstrip()
    if not body:
        raise QueryRejected("The measure counts with `sql` but has no `query`.")
    if ";" in body:
        raise QueryRejected("The query must be a single statement.")
    bare = body.replace(START_PLACEHOLDER, "").replace(END_PLACEHOLDER, "")
    if "{" in bare or "}" in bare:
        raise QueryRejected(
            "The query may use `{start}` and `{end}` and no other braces."
        )
    if END_PLACEHOLDER not in body:
        raise QueryRejected(
            "The query must use `{end}` -- with `{start}` to count what the "
            "week did, or alone for what stood as it closed. Without it every "
            "week shows the same number."
        )
    return body.replace(START_PLACEHOLDER, window.start.isoformat()).replace(
        END_PLACEHOLDER, window.end.isoformat()
    )


def _sql_number(row: Mapping[str, object], name: str) -> float | CountFailed:
    if name not in row:
        return CountFailed(f"The query must return a `{name}` column.")
    value = row[name]
    # An aggregate over no rows is NULL -- `sum()` of nothing, a median of
    # nothing. For a count that means zero, and a median of nothing comes with
    # a total of zero, which scoring reads as nothing to count.
    if value is None:
        return 0.0
    number = _number(value)
    if number is None:
        return CountFailed(f"The query's `{name}` must be a number.")
    return number


def count_from_rows(rows: Sequence[Mapping[str, object]]) -> Counted | CountFailed:
    """Read ``counted`` and ``total`` from the one row a measure's SQL returns."""
    if len(rows) != 1:
        return CountFailed(
            f"The query must return exactly one row; it returned {len(rows)}."
        )
    counted = _sql_number(rows[0], "counted")
    if isinstance(counted, CountFailed):
        return counted
    total = _sql_number(rows[0], "total")
    if isinstance(total, CountFailed):
        return total
    return Counted(counted=counted, total=total)
