"""Where a pod stands against what it owns: the number now, and its weeks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_pod_membership
from app.modules.datastore.services.outcome_standing import (
    MAX_WEEKS,
    WEEKS_SHOWN,
    OutcomeStandingService,
)
from app.modules.datastore.services.wiring import build_outcome_standing_service
from app.modules.pod.contracts.outcome import PodOutcome

router = APIRouter(
    prefix="/pods/{pod_id}/outcome", tags=["query"], redirect_slashes=False
)


def get_outcome_standing_service(uow: UoWDep) -> OutcomeStandingService:
    return build_outcome_standing_service(uow)


OutcomeStandingServiceDep = Annotated[
    OutcomeStandingService, Depends(get_outcome_standing_service)
]


class OutcomeStandingResponse(BaseModel):
    outcome: PodOutcome | None = Field(
        description="What the pod owns, or null when it owns nothing yet."
    )
    value: float | None = Field(
        default=None,
        description=(
            "What its measure answers now, counted over every row for everyone "
            "in the pod alike. Null with no measure, when it answers null, or "
            "when it could not be read (see `problem`)."
        ),
    )
    measured_at: datetime | None = None
    problem: str | None = Field(
        default=None,
        description="Why the measure could not be read, in plain words.",
    )


class OutcomeWeekResponse(BaseModel):
    starts_on: date = Field(description="The Monday the week starts on (UTC).")
    value: float | None = Field(
        default=None,
        description=(
            "The week's last reading. Null for a week it was not read in, or "
            "that answered null."
        ),
    )
    target: float | None = Field(
        default=None, description="The target when the week was read."
    )
    measured_at: datetime | None = Field(
        default=None, description="When `value` was read; null when it never was."
    )
    problem: str | None = Field(
        default=None, description="Why its last reading could not be taken."
    )


class OutcomeHistoryResponse(BaseModel):
    weeks: list[OutcomeWeekResponse] = Field(
        description=(
            "Oldest first, ending with this week. Only the weeks read by the "
            "pod's current measure: changing the measure starts a new history."
        )
    )


@router.get(
    "/standing",
    dependencies=[require_pod_membership("see where the pod stands")],
    response_model=OutcomeStandingResponse,
    status_code=status.HTTP_200_OK,
    operation_id="outcome.standing",
    summary="Where the pod stands",
    description=(
        "What the pod owns and what its measure answers now. The measure is "
        "read over every row of the tables it names, so every member sees the "
        "same number, including members who cannot open some of those rows; "
        "only someone who can read all of them may set it. A measure that "
        "cannot be read comes back as a `problem` rather than an error."
    ),
)
async def get_standing(
    pod_id: UUID, service: OutcomeStandingServiceDep
) -> OutcomeStandingResponse:
    standing = await service.standing(pod_id)
    return OutcomeStandingResponse(
        outcome=standing.outcome,
        value=standing.reading.value if standing.reading else None,
        measured_at=standing.reading.measured_at if standing.reading else None,
        problem=standing.problem,
    )


@router.get(
    "/history",
    dependencies=[require_pod_membership("see where the pod has stood")],
    response_model=OutcomeHistoryResponse,
    status_code=status.HTTP_200_OK,
    operation_id="outcome.history",
    summary="Where the pod has stood, week by week",
    description=(
        "The last `weeks` weeks of the pod's measure, each on its last reading. "
        "The measure is read on a schedule and when it is set; a week it was "
        "never read in has a null value."
    ),
)
async def get_history(
    pod_id: UUID,
    service: OutcomeStandingServiceDep,
    weeks: int = Query(default=WEEKS_SHOWN, ge=1, le=MAX_WEEKS),
) -> OutcomeHistoryResponse:
    slots = await service.history(pod_id, weeks=weeks)
    return OutcomeHistoryResponse(
        weeks=[
            OutcomeWeekResponse(
                starts_on=slot.starts_on,
                value=slot.week.value if slot.week else None,
                target=slot.week.target if slot.week else None,
                measured_at=slot.week.measured_at if slot.week else None,
                problem=slot.week.problem if slot.week else None,
            )
            for slot in slots
        ]
    )
