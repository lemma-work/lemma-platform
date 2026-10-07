"""Wire shapes for a teammate's scorecard: its recent weeks, and the rows behind a
number. Mapped from the domain's results, never handed them directly, so the
wire can change without the counting changing with it."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, model_validator

from app.modules.agent.domain.scorecard import Aim, MeasureStatus, Shape
from app.modules.agent.domain.scorecard_scoring import MeasureResult
from app.modules.agent.domain.scorecard_work import UnitRow
from app.modules.agent.services.scorecard_preview import (
    DEFAULT_PREVIEW_WEEKS,
    MAX_PREVIEW_MEASURES,
    MAX_PREVIEW_WEEKS,
    MeasureHistory,
    ScorecardHistory,
    UnitRows,
)


class ScorecardMeasureSpec(BaseModel):
    """A measure as a `scorecard` row holds it, not yet saved.

    Other columns of a row (`kind`, `is_on`, `note`, ...) are accepted and play
    no part in counting it.
    """

    key: str = Field(default="draft", description="The row's key.")
    measure: str = Field(default="", description="The measure in one sentence.")
    counter: str = Field(
        default="work",
        description=(
            "`work`, `sql`, `approvals`, `standing_work` or `open_questions`."
        ),
    )
    shape: Shape | None = Field(
        default=None,
        description=(
            "`share`, `count`, `median` or `total`. Omitted: a share when `aim` is "
            "higher, a count when it is lower."
        ),
    )
    unit_table: str | None = Field(
        default=None, description="The table with one row per unit of work."
    )
    time_column: str | None = Field(
        default=None,
        description="The DATE or DATETIME column that places a row in a week.",
    )
    test: str | None = Field(
        default=None, description="One SQL boolean expression over a row."
    )
    value: str | None = Field(
        default=None, description="Median or total: one SQL number over a row."
    )
    value_unit: str | None = Field(
        default=None, description="What a count, median or total is in: `minutes`."
    )
    label_column: str | None = Field(
        default=None, description="The column naming a row when it is listed."
    )
    link_column: str | None = Field(
        default=None, description="The column holding a row's link."
    )
    query: str | None = Field(
        default=None,
        description="`sql` counter only: a SELECT returning `counted` and `total`.",
    )
    aim: Aim
    target: float
    target_label: str = ""

    def as_row(self) -> dict[str, object]:
        """The draft as the scorecard row it would be saved as."""
        return self.model_dump(mode="json")


class ScorecardPreviewRequest(BaseModel):
    keys: list[str] | None = Field(
        default=None,
        max_length=MAX_PREVIEW_MEASURES,
        description="Saved measures to preview, by key, whether on or off.",
    )
    measure: ScorecardMeasureSpec | None = Field(
        default=None,
        description="One unsaved measure to preview instead. Nothing is saved.",
    )
    weeks: int = Field(
        default=DEFAULT_PREVIEW_WEEKS,
        ge=1,
        le=MAX_PREVIEW_WEEKS,
        description="How many weeks to count, oldest first.",
    )
    end: date | None = Field(
        default=None,
        description=(
            "The day the last week ends, not included. Defaults to today; "
            "cannot be after it."
        ),
    )

    @model_validator(mode="after")
    def _one_selection(self) -> ScorecardPreviewRequest:
        if self.keys is not None and self.measure is not None:
            raise ValueError("Give `keys` or `measure`, not both.")
        return self


class ScorecardWindow(BaseModel):
    """``[start, end)`` in whole UTC days."""

    start: date
    end: date


class ScorecardWeekScore(BaseModel):
    """One measure's result for one week, as `score_week` records it."""

    key: str
    measure: str
    status: MeasureStatus
    shown: str = Field(
        description='The one string to show: "31 of 40", "none", "1.6 days".'
    )
    target_label: str
    counted: float | None = None
    total: float | None = None
    value: float | None = None
    met: bool | None = None
    reason: str | None = None

    @classmethod
    def of(cls, result: MeasureResult) -> ScorecardWeekScore:
        return cls(
            key=result.key,
            measure=result.measure,
            status=result.status,
            shown=result.shown,
            target_label=result.target_label,
            counted=result.counted,
            total=result.total,
            value=result.value,
            met=result.met,
            reason=result.reason,
        )


class ScorecardMeasureHistory(BaseModel):
    key: str
    measure: str
    weeks: list[ScorecardWeekScore] = Field(description="One per window, oldest first.")

    @classmethod
    def of(cls, history: MeasureHistory) -> ScorecardMeasureHistory:
        return cls(
            key=history.key,
            measure=history.measure,
            weeks=[ScorecardWeekScore.of(week) for week in history.weeks],
        )


class ScorecardPreviewResponse(BaseModel):
    windows: list[ScorecardWindow]
    measures: list[ScorecardMeasureHistory]
    measures_skipped: int = Field(
        default=0, description="Measures chosen but not counted by this preview."
    )

    @classmethod
    def of(cls, history: ScorecardHistory) -> ScorecardPreviewResponse:
        return cls(
            windows=[
                ScorecardWindow(start=window.start, end=window.end)
                for window in history.windows
            ],
            measures=[ScorecardMeasureHistory.of(one) for one in history.measures],
            measures_skipped=history.measures_skipped,
        )


class ScorecardUnitRow(BaseModel):
    id: str
    label: str | None = None
    link: str | None = None
    at: str | None = Field(
        default=None, description="The time column's value, ISO 8601."
    )
    passed: bool | None = Field(
        default=None, description="Whether the number counted this row."
    )

    @classmethod
    def of(cls, row: UnitRow) -> ScorecardUnitRow:
        return cls(
            id=row.id, label=row.label, link=row.link, at=row.at, passed=row.passed
        )


class ScorecardRowsResponse(BaseModel):
    start: date
    end: date
    rows: list[ScorecardUnitRow]
    truncated: bool = Field(
        default=False, description="More rows fell in the week than are listed."
    )

    @classmethod
    def of(cls, found: UnitRows) -> ScorecardRowsResponse:
        return cls(
            start=found.window.start,
            end=found.window.end,
            rows=[ScorecardUnitRow.of(row) for row in found.rows],
            truncated=found.truncated,
        )
