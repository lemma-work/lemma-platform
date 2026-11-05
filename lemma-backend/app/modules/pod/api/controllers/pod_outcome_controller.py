"""What a pod owns: set it, change it, or let it go."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.core.authorization.dependencies import PodContextDep
from app.modules.pod.api.dependencies import (
    OutcomeMeasuresDep,
    PodEditorDep,
    PodOutcomeServiceDep,
)
from app.modules.pod.domain.pod_entities import OutcomeInput, PodOutcome

router = APIRouter(
    prefix="/pods/{pod_id}/outcome", tags=["Pods"], redirect_slashes=False
)


class OutcomeSetResponse(BaseModel):
    outcome: PodOutcome
    value: float | None = Field(
        default=None,
        description=(
            "What a new or changed measure answered when it was checked; null "
            "when it answered null, or was not read because it did not change."
        ),
    )
    measured_at: datetime | None = Field(
        default=None, description="When `value` was read; null when it was not."
    )


@router.put(
    "",
    dependencies=[PodEditorDep],
    response_model=OutcomeSetResponse,
    status_code=status.HTTP_200_OK,
    operation_id="outcome.set",
    summary="Set what the pod owns",
    description=(
        "Set what the pod owns, replacing what it had: a bar held every week "
        "(kind=week) or a finish line by a date (kind=date), with the target "
        "and unit it is held to and the measure that counts it. A measure is "
        "one read-only SQL query over the pod's tables that answers a single "
        "number, read for every member alike; a new or changed one is read "
        "once before it is kept, and refused when the caller cannot read every "
        "row it counts (403), names a table that is not there (404), or does "
        "not answer one number (422)."
    ),
)
async def set_outcome(
    pod_id: UUID,
    data: OutcomeInput,
    service: PodOutcomeServiceDep,
    measures: OutcomeMeasuresDep,
    ctx: PodContextDep,
) -> OutcomeSetResponse:
    kept, reading = await service.set_outcome(pod_id, data, ctx, measures=measures)
    assert kept is not None
    return OutcomeSetResponse(
        outcome=kept,
        value=reading.value if reading else None,
        measured_at=reading.measured_at if reading else None,
    )


@router.delete(
    "",
    dependencies=[PodEditorDep],
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="outcome.clear",
    summary="Clear what the pod owns",
    description=(
        "Let the pod's outcome go. Nothing else about the pod changes, and the "
        "weeks its measure was read in are kept: setting the same measure again "
        "brings back every week no other measure has been read in since."
    ),
)
async def clear_outcome(
    pod_id: UUID,
    service: PodOutcomeServiceDep,
    measures: OutcomeMeasuresDep,
    ctx: PodContextDep,
) -> Response:
    await service.set_outcome(pod_id, None, ctx, measures=measures)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
