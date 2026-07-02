"""Bundle export endpoints.

``POST`` enqueues a streaq job and returns ``202`` with an ``export_id``; the
job assembles the archive and stages it in object storage. ``GET`` is a pure
Redis status read (no DB touched for progress), and ``…/download`` streams the
staged archive with no pooled connection held during the stream.

Domain errors (:class:`PodBundleDomainError` subclasses) carry their own HTTP
status and are surfaced by the global ``DomainError`` handler — controllers just
let them propagate.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from app.core.api.dependencies import CurrentUser
from app.modules.pod.api.dependencies import PodViewerDep
from app.modules.pod_bundle.api.dependencies import ExportUseCasesDep
from app.modules.pod_bundle.api.schemas import (
    ExportStartRequest,
    ExportStatusResponse,
)

router = APIRouter(prefix="/pods", tags=["Pod Bundle"], redirect_slashes=False)


@router.post(
    "/{pod_id}/bundle/exports",
    response_model=ExportStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.export.start",
    summary="Start Pod Export",
    description=(
        "Enqueue a pod export. Returns 202 with an export_id; poll the status "
        "endpoint until READY, then download the bundle archive."
    ),
    dependencies=[PodViewerDep],
)
async def start_export(
    pod_id: UUID,
    data: ExportStartRequest,
    user: CurrentUser,
    use_cases: ExportUseCasesDep,
) -> ExportStatusResponse:
    state = await use_cases.start_export(
        pod_id=pod_id,
        user_id=user.id,
        with_data=data.with_data,
        include=data.include,
    )
    return ExportStatusResponse.from_state(state)


@router.get(
    "/{pod_id}/bundle/exports/{export_id}",
    response_model=ExportStatusResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.bundle.export.get",
    summary="Get Pod Export Status",
    description="Poll the status of a pod export (Redis-only; 410 when expired).",
    dependencies=[PodViewerDep],
)
async def get_export(
    pod_id: UUID,
    export_id: UUID,
    user: CurrentUser,
    use_cases: ExportUseCasesDep,
) -> ExportStatusResponse:
    state = await use_cases.get_export(
        pod_id=pod_id, export_id=export_id, user_id=user.id
    )
    return ExportStatusResponse.from_state(state)


@router.get(
    "/{pod_id}/bundle/exports/{export_id}/download",
    operation_id="pod.bundle.export.download",
    summary="Download Pod Export Bundle",
    description=(
        "Stream the exported bundle archive (application/zip). Available once the "
        "export is READY; 410 if the staged archive was swept."
    ),
    response_class=StreamingResponse,
    dependencies=[PodViewerDep],
)
async def download_export(
    pod_id: UUID,
    export_id: UUID,
    user: CurrentUser,
    use_cases: ExportUseCasesDep,
) -> StreamingResponse:
    # Authorization + state read happen in a short scope inside open_download;
    # the returned iterator streams from object storage with no DB held.
    filename, iterator = await use_cases.open_download(
        pod_id=pod_id, export_id=export_id, user_id=user.id
    )
    return StreamingResponse(
        iterator,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
