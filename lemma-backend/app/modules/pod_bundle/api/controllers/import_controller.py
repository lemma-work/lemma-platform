"""Bundle import endpoints.

``POST`` stages an uploaded archive and enqueues a planning job (``202`` with an
``import_id``); ``GET`` is a pure Redis status read (``410`` when expired); and
``…/events`` streams Server-Sent Events, emitting a full ``snapshot`` frame on
connect (so a late-joining or reconnecting client always sees the whole plan)
followed by live frames — holding no pooled DB connection during the stream.

Domain errors (:class:`PodBundleDomainError` subclasses) carry their own HTTP
status and are surfaced by the global ``DomainError`` handler.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.api.streaming_multipart import (
    MultipartFileLimit,
    stream_multipart_form,
    streaming_multipart_openapi,
)
from app.core.authorization.scope import pod_context_scope
from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import (
    get_channel_service,
)
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.composition.pod_bundle_pod import PodEditorDep, PodViewerDep
from app.modules.pod_bundle.api.dependencies import ImportUseCasesDep
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.api.schemas import (
    ApplyImportRequest,
    ImportStartRequest,
    ImportStatusResponse,
    UploadResponse,
)
from app.modules.pod_bundle.domain.state import IMPORT_TERMINAL_STATUSES
from app.modules.pod_bundle.infrastructure.realtime import bundle_job_channel
from app.modules.pod_bundle.infrastructure.state_store import (
    get_pod_bundle_state_store,
)

router = APIRouter(prefix="/pods", tags=["Pod Bundle"], redirect_slashes=False)

ChannelServiceDep = Annotated[RealtimeChannel, Depends(get_channel_service)]

_TERMINAL_EVENT_TYPES = {"completed", "error", "expired"}


@router.post(
    "/{pod_id}/bundle/imports",
    response_model=ImportStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.import.start",
    summary="Start Pod Import",
    description=(
        "Import a pod bundle from a URL. kind=URL takes a lemma signed download "
        "URL (from an export, or from POST …/bundle/uploads); kind=GITHUB takes a "
        "public repo (repo_url or owner+repo, with account_id for private repos). "
        "Returns 202 with an import_id; poll status until AWAITING_CONFIRMATION, "
        "review the plan, then apply."
    ),
    dependencies=[PodEditorDep],
)
async def start_import(
    pod_id: UUID,
    data: ImportStartRequest,
    user: CurrentUser,
    use_cases: ImportUseCasesDep,
) -> ImportStatusResponse:
    state = await use_cases.start_import(
        pod_id=pod_id,
        user_id=user.id,
        kind=data.kind,
        url=data.url,
        owner=data.owner,
        repo=data.repo,
        ref=data.ref,
        account_id=data.account_id,
    )
    return ImportStatusResponse.from_state(state)


@router.post(
    "/{pod_id}/bundle/uploads",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.bundle.upload",
    summary="Stage A Local Bundle Upload",
    description=(
        "Upload a local .zip bundle and receive a signed lemma download URL to "
        "pass to POST …/bundle/imports as kind=URL. The only multipart endpoint; "
        "it stages bytes and mints a URL, nothing more."
    ),
    dependencies=[PodEditorDep],
    openapi_extra=streaming_multipart_openapi(
        "fastapi___compat__v2__Body_pod__bundle__upload",
        properties={
            "data": {
                "type": "string",
                "format": "binary",
                "contentMediaType": "application/octet-stream",
                "title": "Data",
            }
        },
        required=["data"],
    ),
)
async def upload_bundle(
    pod_id: UUID,
    request: Request,
    user: CurrentUser,
    use_cases: ImportUseCasesDep,
) -> UploadResponse:
    async with stream_multipart_form(
        request,
        file_limits={
            "data": MultipartFileLimit(
                max_bytes=pod_bundle_settings.pod_bundle_max_archive_bytes,
                required=True,
                label="pod bundle",
            )
        },
        combined_max_bytes=pod_bundle_settings.pod_bundle_max_archive_bytes,
    ) as form:
        data = form.require_file("data")
        url, expires_at = await use_cases.stage_upload(
            pod_id=pod_id,
            user_id=user.id,
            filename=data.filename,
            data=data.path,
        )
    return UploadResponse(url=url, expires_at=expires_at)


@router.get(
    "/{pod_id}/bundle/imports/{import_id}",
    response_model=ImportStatusResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.bundle.import.get",
    summary="Get Pod Import Status",
    description="Poll the status + plan of a pod import (Redis-only; 410 when expired).",
    dependencies=[PodViewerDep],
)
async def get_import(
    pod_id: UUID,
    import_id: UUID,
    user: CurrentUser,
    use_cases: ImportUseCasesDep,
) -> ImportStatusResponse:
    state = await use_cases.get_import(
        pod_id=pod_id, import_id=import_id, user_id=user.id
    )
    return ImportStatusResponse.from_state(state)


@router.post(
    "/{pod_id}/bundle/imports/{import_id}/apply",
    response_model=ImportStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.import.apply",
    summary="Apply Pod Import",
    description=(
        "Apply a planned import. Requires confirm_destructive when the plan drops "
        "or alters columns, and resolved values for any required variables. "
        "Returns 202; poll the status endpoint for per-step progress."
    ),
    dependencies=[PodEditorDep],
)
async def apply_import(
    pod_id: UUID,
    import_id: UUID,
    data: ApplyImportRequest,
    user: CurrentUser,
    use_cases: ImportUseCasesDep,
) -> ImportStatusResponse:
    state = await use_cases.apply_import(
        pod_id=pod_id,
        import_id=import_id,
        user_id=user.id,
        variables=data.variables,
        confirm_destructive=data.confirm_destructive,
    )
    return ImportStatusResponse.from_state(state)


@router.post(
    "/{pod_id}/bundle/imports/{import_id}/replan",
    response_model=ImportStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.import.replan",
    summary="Re-plan Pod Import",
    description="Re-run planning against the still-staged bundle (410 if swept).",
    dependencies=[PodEditorDep],
)
async def replan_import(
    pod_id: UUID,
    import_id: UUID,
    user: CurrentUser,
    use_cases: ImportUseCasesDep,
) -> ImportStatusResponse:
    state = await use_cases.replan_import(
        pod_id=pod_id, import_id=import_id, user_id=user.id
    )
    return ImportStatusResponse.from_state(state)


@router.delete(
    "/{pod_id}/bundle/imports/{import_id}",
    response_model=ImportStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.import.cancel",
    summary="Cancel Pod Import",
    description="Abort a running import and delete its state + staged archive.",
    dependencies=[PodEditorDep],
)
async def cancel_import(
    pod_id: UUID,
    import_id: UUID,
    user: CurrentUser,
    use_cases: ImportUseCasesDep,
) -> ImportStatusResponse:
    state = await use_cases.cancel_import(
        pod_id=pod_id, import_id=import_id, user_id=user.id
    )
    return ImportStatusResponse.from_state(state)


@router.get(
    "/{pod_id}/bundle/imports/{import_id}/events",
    operation_id="pod.bundle.import.events",
    summary="Stream Pod Import Progress",
    description=(
        "Server-Sent Events for an import. The first frame is a full state "
        "snapshot; subsequent frames are live status/step/progress updates. The "
        "stream closes when the import reaches a terminal state or expires."
    ),
    response_class=StreamingResponse,
    dependencies=[PodViewerDep],
)
async def stream_import_events(
    pod_id: UUID,
    import_id: UUID,
    user: CurrentUser,
    channel_service: ChannelServiceDep,
    request: Request,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> StreamingResponse:
    # Authorize in a short scope; the stream body holds no pooled connection.
    async with pod_context_scope(
        uow_factory, request=request, user_id=user.id, pod_id=pod_id
    ):
        pass

    store = get_pod_bundle_state_store()
    return StreamingResponse(
        import_event_stream(store, channel_service, pod_id, import_id),
        media_type="text/event-stream",
    )


async def import_event_stream(
    store,
    channel_service: RealtimeChannel,
    pod_id: UUID,
    import_id: UUID,
) -> AsyncGenerator[str, None]:
    """SSE frames for an import: a full ``snapshot`` first (so a late/reconnecting
    client sees the whole plan), then live frames with ``seq <= snapshot`` dropped,
    closing on a terminal event/status. Module-level and dependency-injected so it
    is unit-testable without the FastAPI request machinery."""
    # Subscribe BEFORE reading the snapshot so an event fired in between is not lost.
    async with channel_service.subscribe([bundle_job_channel(import_id)]) as iterator:
        state = await store.get_import(import_id)
        if state is None or state.pod_id != pod_id:
            yield _frame({"type": "expired"})
            return
        snapshot_seq = state.seq
        yield _frame(
            {
                "type": "snapshot",
                "seq": snapshot_seq,
                "state": ImportStatusResponse.from_state(state).model_dump(mode="json"),
            }
        )
        if state.status in IMPORT_TERMINAL_STATUSES:
            return

        terminal_status_values = {s.value for s in IMPORT_TERMINAL_STATUSES}
        async for message in iterator:
            payload = _parse(message)
            if payload is None:
                continue
            if int(payload.get("seq", 0)) <= snapshot_seq:
                continue  # already reflected in the snapshot
            yield _frame(payload)
            if str(payload.get("type", "")) in _TERMINAL_EVENT_TYPES:
                return
            if str(payload.get("status", "")) in terminal_status_values:
                return


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _parse(message: object) -> dict | None:
    try:
        payload = json.loads(message) if isinstance(message, str) else message
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None
