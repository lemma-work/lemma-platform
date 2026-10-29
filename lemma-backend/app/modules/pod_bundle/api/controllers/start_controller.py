"""Starting a Lemma: a pod that owns something, set up by its own agent."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.modules.pod_bundle.api.start_schemas import (
    StartedPodResponse,
    StartLemmaRequest,
    StartLemmaResponse,
)
from app.modules.pod_bundle.application.start_use_cases import (
    StartRequest,
    StartUseCases,
)

router = APIRouter(prefix="/pods", tags=["Pod Bundle"], redirect_slashes=False)


@router.post(
    "/start",
    status_code=status.HTTP_201_CREATED,
    response_model=StartLemmaResponse,
    operation_id="pod.start",
    summary="Start a Lemma",
    description=(
        "Make a pod that owns something, in one transaction: the pod, what it "
        "owns, a first /plan.md, and the chat where its agent sets itself up, "
        "opened with the caller's first message. From a template (`template`, "
        "with `due` for a finish line) it also brings the template's tables, "
        "skill and schedules, applied by a worker, and its agent starts once "
        "they are in; from the caller's own words (`outcome` and `name`) its "
        "agent starts at once. A failure leaves nothing behind. 404 for a "
        "template that is not here; 403 outside the organization; 409 when its "
        "name collides with one made at the same moment."
    ),
)
async def start_lemma(
    data: StartLemmaRequest, user: CurrentUser, uow: UoWDep
) -> StartLemmaResponse:
    started = await StartUseCases(uow).start(
        user_id=user.id,
        request=StartRequest(
            organization_id=data.organization_id,
            template=data.template,
            outcome=data.outcome,
            due=data.due,
            name=data.name,
            description=data.description,
        ),
    )
    pod = started.pod
    return StartLemmaResponse(
        pod=StartedPodResponse(
            id=pod.id,
            organization_id=pod.organization_id,
            name=pod.name,
            description=pod.description,
            icon_url=pod.icon_url,
            created_at=pod.created_at,
            outcome=pod.config.outcome,
        ),
        conversation_id=started.conversation_id,
        import_id=started.import_id,
    )
