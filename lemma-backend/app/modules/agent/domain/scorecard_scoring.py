"""Turning what a counter found into a verdict and the one string to quote.

The shape decides the number -- a share is ``counted / total``; a count, a
median and a total are ``counted`` itself -- and the aim decides whether the number met its
target. ``shown`` is the display string the weekly review quotes, so the model
never has to choose how to write a number down either.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.modules.agent.domain.scorecard import (
    Aim,
    CountFailed,
    Counted,
    CountOutcome,
    Measure,
    MeasureProblem,
    MeasureStatus,
    NotCounted,
    Shape,
)
from app.modules.agent.domain.value_objects import JsonObject

#: A share of fewer rows than this is counted but not judged. With four rows,
#: one row is a quarter of the week, so a "9 in 10" target is met or missed by
#: whichever single row came last -- a verdict on one piece of work, which says
#: nothing about how the teammate is doing. A target of every one is the
#: exception: there a single miss already is the answer, however few rows.
MIN_UNITS_TO_JUDGE = 5


@dataclass(frozen=True, slots=True)
class MeasureResult:
    key: str
    measure: str
    status: MeasureStatus
    shown: str
    target_label: str
    counted: float | None = None
    total: float | None = None
    value: float | None = None
    met: bool | None = None
    reason: str | None = None


def format_number(number: float) -> str:
    """A count as a person writes it: ``1,204``, ``2.5``, never ``2.50``."""
    if float(number).is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _half_up(number: float, places: int) -> Decimal:
    """Rounded the way a person rounds: 2.5 is 3, not Python's banker's 2."""
    step = Decimal(1).scaleb(-places)
    return Decimal(str(number)).quantize(step, rounding=ROUND_HALF_UP)


def format_median(number: float) -> str:
    """A median as a person says it: one decimal under ten, whole above.

    ``1.6`` days is worth its decimal; ``31.4`` minutes is not, and a decimal
    there reads as precision the week does not have. Rounded first, so 9.96
    becomes ``10`` rather than ``10.0``.
    """
    tenths = _half_up(number, 1)
    if abs(tenths) < 10:
        return f"{tenths:.1f}"
    return f"{int(_half_up(number, 0)):,}"


def _with_unit(text: str, unit: str | None, *, exactly_one: bool = False) -> str:
    if not unit:
        return text
    # "1 views" is the one form of a unit nobody writes. Only a count can be
    # exactly one: a median under ten keeps its decimal, and "1.0 days" is
    # already right.
    if exactly_one and unit.endswith("s") and not unit.endswith("ss"):
        unit = unit[:-1]
    return f"{text} {unit}"


def shown_for(
    shape: Shape, counted: float, total: float, *, unit: str | None = None
) -> str:
    """The one display rule, for every measure that has a number.

    A share is shown as its two counts -- ``31 of 40`` -- and never as a
    percentage, so a reader always sees what the share is of: one of one and
    ninety of a hundred are not the same week. A count is shown as itself, and
    zero as ``none``, because for a count of things that went wrong, none is
    the sentence. A median is shown in its unit: ``1.6 days``. A total is shown
    in its unit as written, zero included: ``23,400 impressions``, ``0
    impressions`` -- a week that reached nobody did not have "none" go wrong.
    """
    if shape is Shape.SHARE:
        return f"{format_number(counted)} of {format_number(total)}"
    if shape is Shape.MEDIAN:
        return _with_unit(format_median(counted), unit)
    if shape is Shape.TOTAL:
        return _with_unit(format_number(counted), unit, exactly_one=counted == 1)
    if counted == 0:
        return "none"
    return _with_unit(format_number(counted), unit, exactly_one=counted == 1)


#: What a measure without a number shows instead, so the review never has to
#: invent the words for an absence either.
SHOWN_WITHOUT_NUMBER = {
    MeasureStatus.NOTHING_TO_COUNT: "nothing to count",
    MeasureStatus.NOT_COUNTED: "not counted",
    MeasureStatus.FAILED: "could not count",
}


def _without_number(
    measure: Measure | MeasureProblem, status: MeasureStatus, reason: str | None
) -> MeasureResult:
    return MeasureResult(
        key=measure.key,
        measure=measure.measure,
        status=status,
        shown=SHOWN_WITHOUT_NUMBER[status],
        target_label=measure.target_label,
        reason=reason,
    )


#: The shapes that are a property of the week's rows rather than an amount the
#: rows add up to, so a week without rows has no number at all.
_OF_ROWS = frozenset({Shape.SHARE, Shape.MEDIAN})


def _met(aim: Aim, value: float, target: float) -> bool:
    return value >= target if aim is Aim.HIGHER else value <= target


def _too_few(measure: Measure, total: float) -> bool:
    return (
        measure.shape is Shape.SHARE
        and measure.target < 1
        and total < MIN_UNITS_TO_JUDGE
    )


def _value(measure: Measure, count: Counted) -> float:
    if measure.shape is Shape.SHARE:
        return count.counted / count.total
    return count.counted


def _scored(measure: Measure, count: Counted) -> MeasureResult:
    if measure.shape in _OF_ROWS and count.total <= 0:
        # Nothing was due, so nothing went right or wrong. A share of nothing
        # is neither 0% nor 100%, a median of nothing is not zero, and either
        # would put a verdict on an empty week. The counts are kept: "0 of 0"
        # is still what was found. A count or a total of nothing is zero, and
        # zero is a verdict: a week with no posts reached nobody.
        return replace(
            _without_number(measure, MeasureStatus.NOTHING_TO_COUNT, None),
            counted=count.counted,
            total=count.total,
        )
    value = _value(measure, count)
    if _too_few(measure, count.total):
        return MeasureResult(
            key=measure.key,
            measure=measure.measure,
            status=MeasureStatus.TOO_FEW,
            shown=f"too few to judge ({format_number(count.total)})",
            target_label=measure.target_label,
            counted=count.counted,
            total=count.total,
            value=value,
        )
    return MeasureResult(
        key=measure.key,
        measure=measure.measure,
        status=MeasureStatus.COUNTED,
        shown=shown_for(
            measure.shape, count.counted, count.total, unit=measure.value_unit
        ),
        target_label=measure.target_label,
        counted=count.counted,
        total=count.total,
        value=value,
        met=_met(measure.aim, value, measure.target),
    )


def evaluate(measure: Measure, outcome: CountOutcome) -> MeasureResult:
    """Score one measure from what its counter found.

    The shape gives the value: ``counted / total`` for a share, ``counted``
    itself for a count, a median or a total. The aim gives the verdict: ``higher`` is met
    at or above the target, ``lower`` at or below it. A count's total is kept
    for the record but plays no part.
    """
    if isinstance(outcome, NotCounted):
        return _without_number(measure, MeasureStatus.NOT_COUNTED, None)
    if isinstance(outcome, CountFailed):
        return _without_number(measure, MeasureStatus.FAILED, outcome.reason)
    return _scored(measure, outcome)


def problem_result(problem: MeasureProblem) -> MeasureResult:
    return _without_number(problem, MeasureStatus.FAILED, problem.reason)


def week_row(week: date, result: MeasureResult) -> JsonObject:
    """One ``scorecard_weeks`` row: the week it closes, and what was found."""
    return {
        "week": week.isoformat(),
        "key": result.key,
        "measure": result.measure,
        "counted": result.counted,
        "total": result.total,
        "value": result.value,
        "shown": result.shown,
        "met": result.met,
        "status": result.status.value,
        "target_label": result.target_label,
    }
