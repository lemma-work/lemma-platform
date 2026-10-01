"""Defining, versioning and testing a pod's deciders."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.modules.decisions.api.dependencies import (
    DeciderAskDep,
    DeciderCreateDep,
    DeciderReadDep,
    DeciderResourceDeleteDep,
    DeciderResourceReadDep,
    DeciderResourceUpdateDep,
    DecidersServiceDep,
    asker_from,
    authorize_named_decider,
)
from app.modules.decisions.api.schemas import (
    Agreement,
    CreateDeciderBody,
    DeciderListResponse,
    DeciderResponse,
    DeciderTestBody,
    DeciderTestResponse,
    DeciderVersionListResponse,
    DeciderVersionResponse,
    DisagreementResponse,
    UpdateDeciderBody,
)
from app.modules.decisions.domain.deciders import DeciderEntity
from app.modules.decisions.services.deciders_service import SampleRow, warnings_for
from app.modules.identity.contracts import AuthenticatedUser as UserEntity

router = APIRouter(
    prefix="/pods/{pod_id}/deciders",
    tags=["Decisions"],
    redirect_slashes=False,
)


def _response(decider: DeciderEntity) -> DeciderResponse:
    return DeciderResponse(
        id=decider.id,
        name=decider.name,
        version=decider.version,
        visibility=decider.visibility,
        definition=decider.definition,
        user_id=decider.user_id,
        created_at=decider.created_at,
        updated_at=decider.updated_at,
        warnings=warnings_for(decider.definition),
    )


@router.post(
    "",
    response_model=DeciderResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="decider.create",
    summary="Create a decider",
    description=(
        "Define a named decider: its questions, guidance, input view, rules "
        "and policy. The response lists anything the definition allows but "
        "that is usually a mistake."
    ),
    dependencies=[DeciderCreateDep],
)
async def create_decider(
    request: Request,
    pod_id: UUID,
    body: CreateDeciderBody,
    deciders: DecidersServiceDep,
) -> DeciderResponse:
    user: UserEntity = request.state.user
    created = await deciders.create(
        pod_id=pod_id, user_id=user.id, name=body.name, definition=body.definition
    )
    return _response(created)


@router.get(
    "",
    response_model=DeciderListResponse,
    operation_id="decider.list",
    summary="List deciders",
    dependencies=[DeciderReadDep],
)
async def list_deciders(
    pod_id: UUID,
    deciders: DecidersServiceDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> DeciderListResponse:
    items = await deciders.list(pod_id=pod_id, limit=limit)
    return DeciderListResponse(items=[_response(item) for item in items])


@router.post(
    "/test",
    response_model=DeciderTestResponse,
    operation_id="decider.test",
    summary="Test a decider",
    description=(
        "Decide sample rows with a saved decider or a draft definition, "
        "without recording anything. Rows that carry expected answers are "
        "compared with what the decider said."
    ),
    dependencies=[DeciderAskDep],
)
async def test_decider(
    pod_id: UUID,
    body: DeciderTestBody,
    ctx: PodContextDep,
    uow: UoWDep,
    deciders: DecidersServiceDep,
) -> DeciderTestResponse:
    await authorize_named_decider(
        ctx=ctx, uow=uow, deciders=deciders, pod_id=pod_id, decider=body.decider
    )
    result = await deciders.test(
        decider=body.decider,
        definition=body.definition,
        rows=[SampleRow(state=row.state, expected=row.expected) for row in body.rows],
        asker=asker_from(ctx),
    )
    return DeciderTestResponse(
        answers=result.answers,
        open=result.open,
        agreement={
            key: Agreement(agreed=agreed, total=total)
            for key, (agreed, total) in result.agreement.items()
        },
        disagreements=[
            DisagreementResponse(
                row=item.row,
                question=item.question,
                expected=item.expected,
                answer=item.answer,
            )
            for item in result.disagreements
        ],
    )


@router.get(
    "/{decider_name}",
    response_model=DeciderResponse,
    operation_id="decider.get",
    summary="Get a decider",
    dependencies=[DeciderResourceReadDep],
)
async def get_decider(
    pod_id: UUID,
    decider_name: str,
    deciders: DecidersServiceDep,
) -> DeciderResponse:
    return _response(await deciders.get(pod_id=pod_id, name=decider_name))


@router.put(
    "/{decider_name}",
    response_model=DeciderResponse,
    operation_id="decider.update",
    summary="Save a new version of a decider",
    description=(
        "Replace a decider's definition. The old version is kept, and "
        "decisions made with it still name it."
    ),
    dependencies=[DeciderResourceUpdateDep],
)
async def update_decider(
    request: Request,
    pod_id: UUID,
    decider_name: str,
    body: UpdateDeciderBody,
    deciders: DecidersServiceDep,
) -> DeciderResponse:
    user: UserEntity = request.state.user
    updated = await deciders.update(
        pod_id=pod_id, user_id=user.id, name=decider_name, definition=body.definition
    )
    return _response(updated)


@router.get(
    "/{decider_name}/versions",
    response_model=DeciderVersionListResponse,
    operation_id="decider.version.list",
    summary="List a decider's versions",
    dependencies=[DeciderResourceReadDep],
)
async def list_decider_versions(
    pod_id: UUID,
    decider_name: str,
    deciders: DecidersServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> DeciderVersionListResponse:
    versions = await deciders.versions(pod_id=pod_id, name=decider_name, limit=limit)
    return DeciderVersionListResponse(
        items=[
            DeciderVersionResponse(
                version=item.version,
                definition=item.definition,
                created_by=item.created_by,
                created_at=item.created_at,
            )
            for item in versions
        ]
    )


@router.delete(
    "/{decider_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="decider.delete",
    summary="Delete a decider",
    description=(
        "Delete a decider, its versions and what it learned. Its decisions "
        "stay, naming the decider they were asked of."
    ),
    dependencies=[DeciderResourceDeleteDep],
)
async def delete_decider(
    pod_id: UUID,
    decider_name: str,
    deciders: DecidersServiceDep,
) -> Response:
    await deciders.delete(pod_id=pod_id, name=decider_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
