"""A scorecard's recent weeks, and the rows behind one of its numbers. Read-only.

Two promises are kept here. Every draft measure shows its last few weeks before
anybody keeps it, so a person agrees to a measure having seen what it would
have said -- not a description of what it might say. And every number opens to
the rows behind it, so nobody has to take a share on trust.

Nothing is written: a preview is the weekly count's arithmetic run over earlier
windows, through the same statements (``scorecard_counting``), and handed back.

The work is bounded by :data:`MAX_PREVIEW_WEEKS` windows and
:data:`MAX_PREVIEW_MEASURES` measures, one statement per measure per window --
the same statement ``score_week`` runs for that week, which is the point of
running it per window rather than grouping the weeks into one query.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.modules.agent.domain.errors import (
    ScorecardMeasureNotFoundError,
    ScorecardNotFoundError,
    ScorecardRefusedError,
)
from app.modules.agent.domain.ports import ScorecardSource
from app.modules.agent.domain.scorecard import (
    PLATFORM_COUNTERS,
    CounterName,
    Measure,
    MeasureProblem,
    QueryRejected,
    ScorecardInputError,
    ScorecardPage,
    ScorecardRow,
    ScoringWindow,
    fill_query,
    history_windows,
    measure_from_row,
    read_measures,
    scoring_window,
    select_measures,
)
from app.modules.agent.domain.scorecard_scoring import MeasureResult, evaluate
from app.modules.agent.domain.scorecard_work import (
    UnitRow,
    unit_row,
    work_rows_sql,
)
from app.modules.agent.services.scorecard_counting import (
    MAX_MEASURES,
    PreparedMeasure,
    count_window,
    prepare,
    score_history,
)

#: Four weeks is what a draft is shown before it is kept: long enough to see a
#: trend, short enough that the oldest week is one somebody remembers.
DEFAULT_PREVIEW_WEEKS = 4
MAX_PREVIEW_WEEKS = 8

#: More than a scorecard should ever have; a cap on the statements one preview
#: runs, not a page size.
MAX_PREVIEW_MEASURES = 30

DEFAULT_UNIT_ROWS = 50

NO_ROWS = "SCORECARD_MEASURE_HAS_NO_ROWS"


@dataclass(frozen=True, slots=True)
class MeasureHistory:
    key: str
    measure: str
    #: One result per window, oldest first.
    weeks: list[MeasureResult]


@dataclass(frozen=True, slots=True)
class ScorecardHistory:
    windows: list[ScoringWindow]
    measures: list[MeasureHistory]
    #: Measures chosen but not counted: past the preview cap, or past the rows
    #: of the scorecard that were read.
    measures_skipped: int = 0


@dataclass(frozen=True, slots=True)
class UnitRows:
    window: ScoringWindow
    rows: list[UnitRow]
    #: More rows were placed in the week than were listed.
    truncated: bool


def _windows(end: date | None, *, weeks: int, today: date) -> list[ScoringWindow]:
    if not 1 <= weeks <= MAX_PREVIEW_WEEKS:
        raise ScorecardRefusedError(
            f"`weeks` must be between 1 and {MAX_PREVIEW_WEEKS}."
        )
    try:
        return history_windows(end, weeks=weeks, today=today)
    except ScorecardInputError as refused:
        raise ScorecardRefusedError(str(refused)) from None


async def _scorecard(source: ScorecardSource) -> ScorecardPage:
    page = await source.scorecard_rows(MAX_MEASURES)
    if page is None:
        raise ScorecardNotFoundError()
    return page


async def _histories(
    source: ScorecardSource,
    measures: Sequence[Measure | MeasureProblem],
    windows: Sequence[ScoringWindow],
) -> list[MeasureHistory]:
    return [
        MeasureHistory(
            key=item.key,
            measure=item.measure,
            weeks=await score_history(source, item, windows),
        )
        for item in measures
    ]


async def preview_saved(
    source: ScorecardSource,
    *,
    keys: Sequence[str] | None,
    weeks: int,
    end: date | None,
    today: date,
) -> ScorecardHistory:
    """The scorecard's measures over the last ``weeks`` windows.

    With ``keys``, those measures whatever their state; without, every measure
    that is on and every proposal nobody has kept yet -- what a person deciding
    on the scorecard is looking at.
    """
    windows = _windows(end, weeks=weeks, today=today)
    page = await _scorecard(source)
    unread = max(0, page.total - len(page.rows))
    if keys is None:
        chosen = read_measures(page.rows, proposals=True)
    else:
        chosen, missing = select_measures(page.rows, keys)
        if missing:
            raise ScorecardMeasureNotFoundError(missing)
        unread = 0
    counted = chosen[:MAX_PREVIEW_MEASURES]
    return ScorecardHistory(
        windows=windows,
        measures=await _histories(source, counted, windows),
        measures_skipped=len(chosen) - len(counted) + unread,
    )


_COUNTABLE = ", ".join(f"`{counter.value}`" for counter in CounterName)


async def check_draft(
    source: ScorecardSource, item: Measure | MeasureProblem, window: ScoringWindow
) -> PreparedMeasure:
    """A draft measure that would run, or the reason it would not.

    Stricter than the weekly count, which scores an unknown counter as "not
    counted": a draft is being written right now, by somebody who can still
    fix it, so whatever would stop it counting is said before any week is.
    """
    if isinstance(item, MeasureProblem):
        raise ScorecardRefusedError(item.reason)
    if item.counter not in {counter.value for counter in CounterName}:
        raise ScorecardRefusedError(
            f"`counter` must be one of {_COUNTABLE}; nothing counts "
            f"`{item.counter or '(none)'}`."
        )
    if item.counter == CounterName.SQL:
        try:
            fill_query(item.query, window)
        except QueryRejected as rejected:
            raise ScorecardRefusedError(str(rejected)) from None
    prepared = await prepare(source, item)
    if prepared.refusal is not None:
        raise ScorecardRefusedError(prepared.refusal)
    return prepared


async def preview_draft(
    source: ScorecardSource,
    row: ScorecardRow,
    *,
    weeks: int,
    end: date | None,
    today: date,
) -> ScorecardHistory:
    """One unsaved measure, written as a scorecard row, over the last ``weeks``
    windows -- or :class:`ScorecardRefusedError` with what to change."""
    windows = _windows(end, weeks=weeks, today=today)
    prepared = await check_draft(source, measure_from_row(row), windows[-1])
    measure = prepared.measure
    results = [
        evaluate(measure, await count_window(source, prepared, window))
        for window in windows
    ]
    return ScorecardHistory(
        windows=windows,
        measures=[
            MeasureHistory(key=measure.key, measure=measure.measure, weeks=results)
        ],
    )


def _why_no_rows(measure: Measure) -> str | None:
    if measure.counter == CounterName.WORK:
        return None
    if measure.counter == CounterName.SQL:
        return (
            "This measure has no rows to show: it is counted by its own SQL, "
            "which returns a number, not the rows behind it."
        )
    if measure.counter in PLATFORM_COUNTERS:
        return (
            "This measure has no rows to show: the platform counts it from its "
            "own records, not from a table of the pod's."
        )
    return "This measure has no rows to show: nothing counts it yet."


async def rows_behind(
    source: ScorecardSource,
    key: str,
    *,
    end: date | None,
    limit: int,
    today: date,
) -> UnitRows:
    """The units a work measure counted for the week ending ``end``."""
    try:
        window = scoring_window(end, today=today)
    except ScorecardInputError as refused:
        raise ScorecardRefusedError(str(refused)) from None
    page = await _scorecard(source)
    found, missing = select_measures(page.rows, [key])
    if missing or not found:
        raise ScorecardMeasureNotFoundError(missing or [key])
    item = found[0]
    if isinstance(item, MeasureProblem):
        raise ScorecardRefusedError(item.reason)
    reason = _why_no_rows(item)
    if reason is not None:
        raise ScorecardRefusedError(reason, code=NO_ROWS)
    prepared = await prepare(source, item)
    if prepared.spec is None:
        raise ScorecardRefusedError(prepared.refusal or "This measure cannot run.")
    try:
        sql = work_rows_sql(prepared.spec, window.start, window.end, limit)
    except QueryRejected as rejected:
        raise ScorecardRefusedError(str(rejected)) from None
    rows = await source.query(sql)
    return UnitRows(
        window=window,
        rows=[unit_row(row) for row in rows[:limit]],
        truncated=len(rows) > limit,
    )


__all__ = [
    "DEFAULT_PREVIEW_WEEKS",
    "DEFAULT_UNIT_ROWS",
    "MAX_PREVIEW_MEASURES",
    "MAX_PREVIEW_WEEKS",
    "MeasureHistory",
    "ScorecardHistory",
    "UnitRows",
    "check_draft",
    "preview_draft",
    "preview_saved",
    "rows_behind",
]
