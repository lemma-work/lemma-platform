"""Bundle export endpoints.

``POST`` enqueues a streaq job and returns ``202`` with an ``export_id``; the
job assembles the archive, stages it in object storage, and mints a signed
download URL. ``GET`` is a pure Redis status read (no DB touched for progress)
that surfaces the ``download_url``. ``GET /pods/bundle/download`` streams the
archive: it requires an authenticated lemma user (any logged-in user, not
pod-scoped) AND a valid signed token — a double gate — and holds no pooled
connection during the stream.

Domain errors (:class:`PodBundleDomainError` subclasses) carry their own HTTP
status and are surfaced by the global ``DomainError`` handler.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from app.core.api.dependencies import CurrentUser
from app.composition.pod_bundle_pod import PodViewerDep
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
        "endpoint until READY, then fetch the signed download_url."
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
        data_tables=data.data_tables,
        with_files=data.with_files,
        include=data.include,
        ttl_seconds=data.ttl_seconds,
    )
    return ExportStatusResponse.from_state(state)


@router.get(
    "/{pod_id}/bundle/exports/{export_id}",
    response_model=ExportStatusResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.bundle.export.get",
    summary="Get Pod Export Status",
    description=(
        "Poll the status of a pod export (Redis-only; 410 when expired). When "
        "READY, includes the signed download_url, its expires_at, and any "
        "data-cap warnings."
    ),
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
    "/bundle/download",
    operation_id="pod.bundle.download",
    summary="Download A Bundle Archive",
    description=(
        "Stream a bundle archive (application/zip) by signed token. Requires an "
        "authenticated lemma user AND a valid token; not pod-scoped, so a share "
        "link works for any signed-in user. 410 if the token is invalid/expired "
        "or the archive was swept."
    ),
    response_class=StreamingResponse,
    # NOT pod-scoped: `user: CurrentUser` requires auth; the token is verified in
    # the use case. Kept out of EXCLUDED_PATHS so the global auth gate applies.
)
async def download_bundle(
    user: CurrentUser,
    use_cases: ExportUseCasesDep,
    token: str = Query(..., description="Signed download token."),
) -> StreamingResponse:
    filename, iterator = await use_cases.open_download_by_token(token)
    return StreamingResponse(
        iterator,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
