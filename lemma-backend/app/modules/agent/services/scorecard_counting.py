"""Counting a scorecard's measures over a window, as one caller.

One path for every reader of a number: the weekly count ``score_week`` writes,
the history a draft is shown before anybody keeps it, and the preview the
setup screen draws. A number previewed and the number later recorded for the
same week come from the same statement, so the two cannot disagree.

Everything is read through a :class:`ScorecardSource`, which answers as the
caller; nothing here decides who may see what.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.domain.errors import DomainError
from app.modules.agent.domain.ports import ScorecardSource
from app.modules.agent.domain.scorecard import (
    PLATFORM_COUNTERS,
    CountFailed,
    Counted,
    CounterName,
    CountOutcome,
    Measure,
    MeasureProblem,
    NotCounted,
    QueryRejected,
    ScoringWindow,
    count_from_rows,
    fill_query,
)
from app.modules.agent.domain.scorecard_scoring import (
    MeasureResult,
    evaluate,
    problem_result,
)
from app.modules.agent.domain.scorecard_work import (
    WorkSpec,
    check_identifier,
    work_count_sql,
    work_spec,
)

#: A scorecard is a handful of lines. Past this many rows it is a report, and
#: whoever reads it is told how many were not read rather than losing them
#: silently.
MAX_MEASURES = 100


@dataclass(frozen=True, slots=True)
class PreparedMeasure:
    """A measure with everything that does not change from week to week
    settled: a work measure's checked SQL parts, or why it cannot run."""

    measure: Measure
    spec: WorkSpec | None = None
    refusal: str | None = None


async def _work_spec(source: ScorecardSource, measure: Measure) -> WorkSpec:
    name = check_identifier("unit_table", measure.work.unit_table)
    table = await source.unit_table(name)
    if table is None:
        raise QueryRejected(
            f"`unit_table` names `{name}`, and this pod has no table by that name."
        )
    return work_spec(measure.work, measure.shape, table)


async def prepare(source: ScorecardSource, measure: Measure) -> PreparedMeasure:
    """Check a measure once, however many weeks it is then counted over.

    A work measure is checked against its table's real columns here, so a
    measure that names a column the table does not have is refused with that
    reason before any statement is built. A table the caller may not read is
    refused the same way, with the datastore's reason.
    """
    if measure.counter != CounterName.WORK:
        return PreparedMeasure(measure)
    try:
        return PreparedMeasure(measure, spec=await _work_spec(source, measure))
    except QueryRejected as rejected:
        return PreparedMeasure(measure, refusal=str(rejected))
    except DomainError as refused:
        return PreparedMeasure(measure, refusal=refused.message)


async def _count_by_statement(
    source: ScorecardSource, sql: str
) -> Counted | CountFailed:
    """One counting statement. A refusal -- a table that is gone, a column that
    is not, a table the caller may not read -- fails this measure and not the
    week: the other measures were counted, and the review should say which one
    could not be."""
    try:
        rows = await source.query(sql)
    except DomainError as refused:
        return CountFailed(refused.message)
    return count_from_rows(rows)


async def count_window(
    source: ScorecardSource, prepared: PreparedMeasure, window: ScoringWindow
) -> CountOutcome:
    """Run a prepared measure's counter over one window."""
    measure = prepared.measure
    if prepared.refusal is not None:
        return CountFailed(prepared.refusal)
    if prepared.spec is not None:
        return await _count_by_statement(
            source, work_count_sql(prepared.spec, window.start, window.end)
        )
    if measure.counter in PLATFORM_COUNTERS:
        return await source.count_platform(CounterName(measure.counter), window)
    if measure.counter == CounterName.SQL:
        try:
            sql = fill_query(measure.query, window)
        except QueryRejected as rejected:
            return CountFailed(str(rejected))
        return await _count_by_statement(source, sql)
    return NotCounted()


async def score_history(
    source: ScorecardSource,
    item: Measure | MeasureProblem,
    windows: Sequence[ScoringWindow],
) -> list[MeasureResult]:
    """One measure scored over each window, in the order given."""
    if isinstance(item, MeasureProblem):
        return [problem_result(item) for _ in windows]
    prepared = await prepare(source, item)
    return [
        evaluate(item, await count_window(source, prepared, window))
        for window in windows
    ]


async def score_window(
    source: ScorecardSource,
    measures: Sequence[Measure | MeasureProblem],
    window: ScoringWindow,
) -> list[MeasureResult]:
    """Every measure scored over one window, in the scorecard's order."""
    results: list[MeasureResult] = []
    for item in measures:
        results.extend(await score_history(source, item, [window]))
    return results


__all__ = [
    "MAX_MEASURES",
    "PreparedMeasure",
    "count_window",
    "prepare",
    "score_history",
    "score_window",
]
