"""Asking for a document to be read again.

Its own router rather than a route in ``file_controller`` because that file is
at the architecture ratchet's size ceiling. Same prefix and tag, so to a client
it is one more file operation.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.core.api.dependencies import CurrentUser
from app.modules.datastore.api.dependencies import FileUseCasesDep
from app.modules.datastore.api.file_response_mapping import (
    ensure_file_in_pod,
    to_file_response,
)
from app.modules.datastore.api.schemas.datastore_schemas import FileDetailResponse

router = APIRouter(
    prefix="/pods/{pod_id}/datastore/files",
    tags=["files"],
    redirect_slashes=False,
)


@router.post(
    "/by-path/retry-processing",
    response_model=FileDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.retry_processing",
    summary="Retry File Processing",
    description=(
        "Queue a document whose processing failed to be read and indexed "
        "again, with a fresh retry budget. A file that did not fail is "
        "returned unchanged."
    ),
)
async def retry_file_processing(
    pod_id: UUID,
    request: Request,
    user: CurrentUser,
    use_cases: FileUseCasesDep,
    path: str = Query(...),
) -> FileDetailResponse:
    file_entity = await use_cases.retry_processing(
        pod_id=pod_id,
        path=path,
        request=request,
        user_id=user.id,
    )
    response = FileDetailResponse(
        **to_file_response(file_entity, user.id).model_dump(),
        allowed_actions=file_entity.allowed_actions,
    )
    ensure_file_in_pod(response, pod_id)
    return response
