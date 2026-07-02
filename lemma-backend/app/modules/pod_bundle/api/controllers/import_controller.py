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

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.authorization.scope import pod_context_scope
from app.core.infrastructure.channels.channel_service import (
    ChannelService,
    get_channel_service,
)
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.pod.api.dependencies import PodEditorDep, PodViewerDep
from app.modules.pod_bundle.api.dependencies import ImportUseCasesDep
from app.modules.pod_bundle.api.schemas import ImportStatusResponse
from app.modules.pod_bundle.domain.state import IMPORT_TERMINAL_STATUSES
from app.modules.pod_bundle.infrastructure.realtime import bundle_job_channel
from app.modules.pod_bundle.infrastructure.state_store import (
    get_pod_bundle_state_store,
)

router = APIRouter(prefix="/pods", tags=["Pod Bundle"], redirect_slashes=False)

ChannelServiceDep = Annotated[ChannelService, Depends(get_channel_service)]

_TERMINAL_EVENT_TYPES = {"completed", "error", "expired"}


@router.post(
    "/{pod_id}/bundle/imports",
    response_model=ImportStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.import.start",
    summary="Start Pod Import",
    description=(
        "Upload a pod bundle (.zip) and enqueue planning. Returns 202 with an "
        "import_id; poll the status endpoint until AWAITING_CONFIRMATION, review "
        "the plan, then apply."
    ),
    dependencies=[PodEditorDep],
)
async def start_import(
    pod_id: UUID,
    user: CurrentUser,
    use_cases: ImportUseCasesDep,
    data: UploadFile = File(...),
) -> ImportStatusResponse:
    content = await data.read()
    state = await use_cases.start_upload_import(
        pod_id=pod_id, user_id=user.id, filename=data.filename, data=content
    )
    return ImportStatusResponse.from_state(state)


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
    channel_service: ChannelService,
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
