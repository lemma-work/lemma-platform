"""Asking decisions, reading them, and answering them."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.api.dependencies import UoWDep

from app.core.authorization.context import ActorType, Context
from app.core.authorization.dependencies import PodContextDep
from app.modules.decisions.api.dependencies import (
    DecidersServiceDep,
    DeciderAskDep,
    DeciderReadDep,
    DecisionsServiceDep,
    asker_from,
    authorize_named_decider,
)
from app.modules.decisions.api.schemas import (
    AnswerBody,
    DecideBody,
    DecideRowsBody,
    DecisionListResponse,
    DecisionResponse,
    RowResultResponse,
    RowsResponse,
)
from app.modules.decisions.domain.decisions import DecisionEntity, Rung
from app.modules.decisions.domain.errors import DecisionNeedsPersonError
from app.modules.decisions.services.decisions_service import DecideRequest

router = APIRouter(
    prefix="/pods/{pod_id}/decisions",
    tags=["Decisions"],
    redirect_slashes=False,
)


def _response(decision: DecisionEntity) -> DecisionResponse:
    return DecisionResponse.model_validate(decision)


@router.post(
    "",
    response_model=DecisionResponse,
    status_code=status.HTTP_200_OK,
    operation_id="decision.create",
    summary="Ask a decision",
    description=(
        "Ask a decider -- a pod decider by name, `system:<name>`, or questions "
        "passed inline -- about one state. With a `subject`, a decider is asked "
        "once: asking again returns the recorded decision."
    ),
    dependencies=[DeciderAskDep],
)
async def create_decision(
    pod_id: UUID,
    body: DecideBody,
    ctx: PodContextDep,
    uow: UoWDep,
    decisions: DecisionsServiceDep,
    deciders: DecidersServiceDep,
) -> DecisionResponse:
    await authorize_named_decider(
        ctx=ctx, uow=uow, deciders=deciders, pod_id=pod_id, decider=body.decider
    )
    decision = await decisions.decide(
        DecideRequest(
            decider=body.decider,
            definition=body.definition,
            state=body.state,
            subject=body.subject,
            options=body.options,
            record=body.record,
        ),
        asker_from(ctx, body.visibility),
    )
    return _response(decision)


@router.post(
    "/rows",
    response_model=RowsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="decision.rows",
    summary="Decide many rows",
    description=(
        "Ask one decider about many rows at once, for sorting a list rather "
        "than one event. Rows are decided in parallel within the bulk budget."
    ),
    dependencies=[DeciderAskDep],
)
async def decide_rows(
    pod_id: UUID,
    body: DecideRowsBody,
    ctx: PodContextDep,
    uow: UoWDep,
    decisions: DecisionsServiceDep,
    deciders: DecidersServiceDep,
) -> RowsResponse:
    await authorize_named_decider(
        ctx=ctx, uow=uow, deciders=deciders, pod_id=pod_id, decider=body.decider
    )
    result = await decisions.decide_rows(
        decider=body.decider,
        definition=body.definition,
        rows=body.rows,
        asker=asker_from(ctx, body.visibility),
        options=body.options,
        id_field=body.id_field,
        subject_prefix=body.subject_prefix,
        record=body.record,
    )
    return RowsResponse(
        decider_key=result.decider_key,
        rows=[
            RowResultResponse(
                index=row.index,
                row_id=row.row_id,
                answers=row.answers,
                open=row.open,
                decision_id=row.decision_id,
                failed=row.failed,
            )
            for row in result.rows
        ],
        counts=result.counts,
        failed=result.failed,
    )


@router.get(
    "",
    response_model=DecisionListResponse,
    operation_id="decision.list",
    summary="List decisions",
    description=(
        "Decisions in this pod, newest first: the ones shared with the pod and "
        "your own."
    ),
    dependencies=[DeciderReadDep],
)
async def list_decisions(
    pod_id: UUID,
    ctx: PodContextDep,
    decisions: DecisionsServiceDep,
    decider: str | None = Query(
        default=None, description="Only this decider's decisions."
    ),
    open_only: bool = Query(
        default=False, description="Only decisions with a question left open."
    ),
    before: datetime | None = Query(
        default=None, description="Only decisions made before this time."
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> DecisionListResponse:
    viewer_id = ctx.user_id
    if viewer_id is None:
        return DecisionListResponse(items=[])
    items = await decisions.list(
        pod_id=pod_id,
        viewer_id=viewer_id,
        decider=decider,
        open_only=open_only,
        before=before,
        limit=limit,
    )
    return DecisionListResponse(
        items=[_response(item) for item in items],
        next_before=items[-1].created_at if len(items) == limit else None,
    )


@router.get(
    "/{decision_id}",
    response_model=DecisionResponse,
    operation_id="decision.get",
    summary="Get a decision",
    dependencies=[DeciderReadDep],
)
async def get_decision(
    pod_id: UUID,
    decision_id: UUID,
    ctx: PodContextDep,
    decisions: DecisionsServiceDep,
) -> DecisionResponse:
    decision = await decisions.get(
        decision_id=decision_id, pod_id=pod_id, viewer_id=_viewer(ctx)
    )
    return _response(decision)


@router.post(
    "/{decision_id}/answer",
    response_model=DecisionResponse,
    operation_id="decision.answer",
    summary="Answer a decision",
    description=(
        "Answer questions a decision left open, or correct a machine's answer. "
        "A person's answer becomes an example the decider learns from."
    ),
    dependencies=[DeciderAskDep],
)
async def answer_decision(
    pod_id: UUID,
    decision_id: UUID,
    body: AnswerBody,
    ctx: PodContextDep,
    decisions: DecisionsServiceDep,
) -> DecisionResponse:
    decision = await decisions.answer(
        decision_id=decision_id,
        pod_id=pod_id,
        answers=body.answers,
        by=_answerer(ctx, body.by),
        user_id=_viewer(ctx),
    )
    return _response(decision)


def _answerer(ctx: Context, claimed: str) -> Rung:
    """Only a person signed in as themselves teaches a decider.

    Anything arriving through a workload -- an agent relaying what it says the
    person told it -- is recorded as the agent's answer, whatever the body
    claims. Otherwise text an agent read could have it "relay" a correction and
    teach the decider something no person said.
    """
    if ctx.actor_type is not ActorType.USER:
        return Rung.AGENT
    return Rung.PERSON if claimed == "person" else Rung.AGENT


def _viewer(ctx: Context) -> UUID:
    if ctx.user_id is None:
        # Pod routes are reached by people and by workloads acting for one;
        # neither arrives without a user, so this is a caller we cannot place.
        raise DecisionNeedsPersonError()
    return ctx.user_id
