"""GitHub publish endpoints.

``POST`` enqueues a publish job (``202`` with a ``publish_id``); ``GET`` is a
pure Redis status read; ``…/events`` streams SSE progress (snapshot then live).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.authorization.scope import pod_context_scope
from app.core.infrastructure.channels.channel_service import (
    ChannelService,
    get_channel_service,
)
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.pod.api.dependencies import PodViewerDep
from app.modules.pod_bundle.api.dependencies import PublishUseCasesDep
from app.modules.pod_bundle.api.schemas import (
    PublishStartRequest,
    PublishStatusResponse,
)
from app.modules.pod_bundle.domain.state import PublishStatus
from app.modules.pod_bundle.infrastructure.realtime import bundle_job_channel
from app.modules.pod_bundle.infrastructure.state_store import (
    get_pod_bundle_state_store,
)

router = APIRouter(prefix="/pods", tags=["Pod Bundle"], redirect_slashes=False)

ChannelServiceDep = Annotated[ChannelService, Depends(get_channel_service)]

_TERMINAL = {PublishStatus.COMPLETED, PublishStatus.FAILED}
_TERMINAL_EVENT_TYPES = {"completed", "error", "expired"}


@router.post(
    "/{pod_id}/bundle/publishes",
    response_model=PublishStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.publish.start",
    summary="Publish Pod To GitHub",
    description=(
        "Publish the pod as a bundle to a new GitHub repository. Returns 202 with "
        "a publish_id; poll the status endpoint for the repo URL."
    ),
    dependencies=[PodViewerDep],
)
async def start_publish(
    pod_id: UUID,
    data: PublishStartRequest,
    user: CurrentUser,
    use_cases: PublishUseCasesDep,
) -> PublishStatusResponse:
    state = await use_cases.start_publish(
        pod_id=pod_id,
        user_id=user.id,
        repo_name=data.repo_name,
        private=data.private,
        account_id=data.account_id,
        ai_readme=data.ai_readme,
    )
    return PublishStatusResponse.from_state(state)


@router.get(
    "/{pod_id}/bundle/publishes/{publish_id}",
    response_model=PublishStatusResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.bundle.publish.get",
    summary="Get Pod Publish Status",
    description="Poll the status of a pod publish (Redis-only; 410 when expired).",
    dependencies=[PodViewerDep],
)
async def get_publish(
    pod_id: UUID,
    publish_id: UUID,
    user: CurrentUser,
    use_cases: PublishUseCasesDep,
) -> PublishStatusResponse:
    state = await use_cases.get_publish(
        pod_id=pod_id, publish_id=publish_id, user_id=user.id
    )
    return PublishStatusResponse.from_state(state)


@router.get(
    "/{pod_id}/bundle/publishes/{publish_id}/events",
    operation_id="pod.bundle.publish.events",
    summary="Stream Pod Publish Progress",
    description="Server-Sent Events for a publish (snapshot then live frames).",
    response_class=StreamingResponse,
    dependencies=[PodViewerDep],
)
async def stream_publish_events(
    pod_id: UUID,
    publish_id: UUID,
    user: CurrentUser,
    channel_service: ChannelServiceDep,
    request: Request,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> StreamingResponse:
    async with pod_context_scope(
        uow_factory, request=request, user_id=user.id, pod_id=pod_id
    ):
        pass

    store = get_pod_bundle_state_store()
    return StreamingResponse(
        publish_event_stream(store, channel_service, pod_id, publish_id),
        media_type="text/event-stream",
    )


async def publish_event_stream(
    store, channel_service: ChannelService, pod_id: UUID, publish_id: UUID
) -> AsyncGenerator[str, None]:
    async with channel_service.subscribe([bundle_job_channel(publish_id)]) as iterator:
        state = await store.get_publish(publish_id)
        if state is None or state.pod_id != pod_id:
            yield _frame({"type": "expired"})
            return
        snapshot_seq = state.seq
        yield _frame(
            {
                "type": "snapshot",
                "seq": snapshot_seq,
                "state": PublishStatusResponse.from_state(state).model_dump(mode="json"),
            }
        )
        if state.status in _TERMINAL:
            return
        terminal_status_values = {s.value for s in _TERMINAL}
        async for message in iterator:
            payload = _parse(message)
            if payload is None or int(payload.get("seq", 0)) <= snapshot_seq:
                continue
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
