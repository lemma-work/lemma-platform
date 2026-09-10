"""Function revision endpoints: history, one revision, and promotion.

Split out of ``function_controller`` because that module is at the architecture
ratchet's per-file ceiling. The router carries the same prefix and the same
``Functions`` tag, so these are one API surface however the modules divide.
"""

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.modules.identity.contracts import AuthenticatedUser as UserEntity
from app.modules.function.api.dependencies import FunctionUseCasesDep
from app.modules.function.api.schemas.function_schemas import (
    FunctionRevisionListResponse,
    FunctionRevisionPromoteResponse,
    FunctionRevisionResponse,
)

router = APIRouter(
    prefix="/pods/{pod_id}/functions",
    tags=["Functions"],
    redirect_slashes=False,
)


def _revision_response(revision, *, is_live: bool, detail: bool = False):
    payload = {
        "id": revision.id,
        "function_id": revision.function_id,
        "revision_number": revision.revision_number,
        "revision_hash": revision.revision_hash,
        "label": revision.label,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
        "is_live": is_live,
        "pruned_at": revision.pruned_at,
    }
    if detail:
        payload |= {
            "code": revision.code,
            "input_schema": revision.input_schema,
            "output_schema": revision.output_schema,
            "config_schema": revision.config_schema,
        }
    return FunctionRevisionResponse(**payload)


@router.get(
    "/{function_name}/revisions",
    response_model=FunctionRevisionListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.revision.list",
    summary="List Function Revisions",
    description="List the built revisions of a function, newest first.",
)
async def list_function_revisions(
    request: Request,
    pod_id: UUID,
    function_name: str,
    use_cases: FunctionUseCasesDep,
) -> FunctionRevisionListResponse:
    user: UserEntity = request.state.user
    listings = await use_cases.list_revisions(
        pod_id=pod_id, name=function_name, user_id=user.id, request=request
    )
    return FunctionRevisionListResponse(
        items=[
            _revision_response(entry.revision, is_live=entry.is_live)
            for entry in listings
        ]
    )


@router.get(
    "/{function_name}/revisions/{revision_ref}",
    response_model=FunctionRevisionResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.revision.get",
    summary="Get Function Revision",
    description=(
        "Read one revision, including its source and the schemas its code "
        "implements. A revision may be addressed by number ('r12') or hash."
    ),
)
async def get_function_revision(
    request: Request,
    pod_id: UUID,
    function_name: str,
    revision_ref: str,
    use_cases: FunctionUseCasesDep,
) -> FunctionRevisionResponse:
    user: UserEntity = request.state.user
    revision, is_live = await use_cases.get_revision(
        pod_id=pod_id,
        name=function_name,
        ref=revision_ref,
        user_id=user.id,
        request=request,
    )
    return _revision_response(revision, is_live=is_live, detail=True)


@router.post(
    "/{function_name}/revisions/{revision_ref}/promote",
    response_model=FunctionRevisionPromoteResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.revision.promote",
    summary="Promote Function Revision",
    description=(
        "Make an existing revision the live one. Its input/output/config "
        "schemas are restored with it, since they are the contract its code "
        "implements."
    ),
)
async def promote_function_revision(
    request: Request,
    pod_id: UUID,
    function_name: str,
    revision_ref: str,
    use_cases: FunctionUseCasesDep,
) -> FunctionRevisionPromoteResponse:
    user: UserEntity = request.state.user
    result = await use_cases.promote_revision(
        pod_id=pod_id,
        name=function_name,
        ref=revision_ref,
        user_id=user.id,
        request=request,
    )
    return FunctionRevisionPromoteResponse(
        revision=_revision_response(result.revision, is_live=True),
        schema_changed=result.schema_changed,
    )
