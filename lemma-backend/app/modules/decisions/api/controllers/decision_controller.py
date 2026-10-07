"""`POST /pods/{pod_id}/decisions`: ask closed questions about some evidence."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.authorization.dependencies import PodContextDep, require_pod_membership
from app.modules.decisions.api.dependencies import (
    DecisionServiceDep,
    bounded_body,
    caller_from_context,
)
from app.modules.decisions.api.schemas import DecisionResponse, MakeDecisionRequest
from app.modules.decisions.domain.errors import DecisionLimitedError

router = APIRouter(prefix="/pods/{pod_id}/decisions", tags=["Decisions"])


@router.post(
    "",
    response_model=DecisionResponse,
    status_code=status.HTTP_200_OK,
    operation_id="decision.make",
    summary="Make a decision",
    description=(
        "Answer closed questions -- a choice, several choices, yes or no, a "
        "point on a scale -- about one piece of evidence. Nothing is stored: "
        "record the answer wherever it matters to you. An answer of null means "
        "the evidence did not support one. 422 means the request cannot be "
        "asked as sent; 429 and 503 mean ask again later."
    ),
    dependencies=[
        Depends(bounded_body),
        require_pod_membership("make decisions in this pod"),
    ],
    responses={
        413: {"description": "The request body is too large."},
        422: {"description": "The questions, evidence or examples are not valid."},
        429: {"description": "Rate or spend limit reached; see Retry-After."},
        503: {"description": "The decision provider did not answer; retry."},
    },
)
async def make_decision(
    pod_id: UUID,
    body: MakeDecisionRequest,
    ctx: PodContextDep,
    service: DecisionServiceDep,
) -> DecisionResponse:
    del pod_id  # bound into ctx by PodContextDep
    try:
        result = await service.decide(body.to_request(), caller_from_context(ctx))
    except DecisionLimitedError as exc:
        # Raised as an HTTPException because only that handler sends headers,
        # and a 429 a client cannot schedule its retry from is half an answer.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    return DecisionResponse.from_result(result)
