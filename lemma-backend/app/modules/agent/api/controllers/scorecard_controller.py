"""A teammate's scorecard, read: its recent weeks, and the rows behind a number.

Both routes only read. The weekly count that writes ``scorecard_weeks`` is the
``score_week`` tool's; these run the same counting over earlier windows and
hand the result back, as the person asking, so a scorecard can be shown -- and
a draft measure judged -- before anything is recorded.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.modules.agent.api.dependencies import ScorecardReaderDep, ScorecardSourceDep
from app.modules.agent.api.scorecard_schemas import (
    ScorecardPreviewRequest,
    ScorecardPreviewResponse,
    ScorecardRowsResponse,
)
from app.modules.agent.domain.scorecard_work import MAX_UNIT_ROWS
from app.modules.agent.services.scorecard_preview import (
    DEFAULT_UNIT_ROWS,
    preview_draft,
    preview_saved,
    rows_behind,
)

router = APIRouter(prefix="/pods/{pod_id}/scorecard", tags=["agents"])


def _today() -> date:
    return datetime.now(timezone.utc).date()


@router.post(
    "/preview",
    response_model=ScorecardPreviewResponse,
    status_code=status.HTTP_200_OK,
    operation_id="agent.scorecard.preview",
    dependencies=[ScorecardReaderDep],
    summary="Preview Scorecard Weeks",
    description=(
        "Count scorecard measures over recent weeks without recording anything: "
        "the same seven-day windows and the same counting the weekly review "
        "uses, oldest week first. With `measure`, one unsaved measure (a dry "
        "run); with `keys`, those saved measures; with neither, every measure "
        "that is on and every proposal not yet kept. Counted as the caller."
    ),
    responses={
        404: {"description": "The pod has no scorecard, or no measure has a key"},
        422: {"description": "A draft measure or a window that cannot be counted"},
    },
)
async def preview_scorecard(
    pod_id: UUID,
    data: ScorecardPreviewRequest,
    source: ScorecardSourceDep,
) -> ScorecardPreviewResponse:
    if data.measure is not None:
        history = await preview_draft(
            source,
            data.measure.as_row(),
            weeks=data.weeks,
            end=data.end,
            today=_today(),
        )
    else:
        history = await preview_saved(
            source, keys=data.keys, weeks=data.weeks, end=data.end, today=_today()
        )
    return ScorecardPreviewResponse.of(history)


@router.get(
    "/measures/{key}/rows",
    response_model=ScorecardRowsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="agent.scorecard.measure_rows",
    dependencies=[ScorecardReaderDep],
    summary="List Rows Behind A Scorecard Measure",
    description=(
        "The units of work a measure counted for the week ending `end` "
        "(default today), with whether each passed its test. Only a `work` "
        "measure has rows; any other is answered 422."
    ),
    responses={
        404: {"description": "The pod has no scorecard, or no measure has this key"},
        422: {"description": "The measure has no rows to show, or cannot run"},
    },
)
async def list_measure_rows(
    pod_id: UUID,
    key: str,
    source: ScorecardSourceDep,
    end: date | None = Query(
        default=None,
        description="The day the week ends, not included. Defaults to today.",
    ),
    limit: int = Query(
        default=DEFAULT_UNIT_ROWS,
        ge=1,
        le=MAX_UNIT_ROWS,
        description="How many rows to list at most.",
    ),
) -> ScorecardRowsResponse:
    found = await rows_behind(source, key, end=end, limit=limit, today=_today())
    return ScorecardRowsResponse.of(found)
