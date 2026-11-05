"""Calls: open one over HTTP, then hold it on a WebSocket.

The socket carries the caller's microphone up as binary frames (16 kHz, 16-bit
mono PCM) and the voice down the same way, at the rate the ``ready`` frame
names; everything else is JSON text frames. Audio on a socket is the exception
to "bulk bytes travel over HTTP": it is a continuous stream of small frames
that must arrive in order and on time, which is what a socket is for.

The first text frame is ``start``: who the caller is talking to, the pod, and
what came before the call, for the voice's brief. The coordinator's brief is the
conversation's own, set when the call was opened.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, WebSocket, status

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.authorization.dependencies import require_pod_membership
from app.core.authorization.scope import pod_context_scope
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.live.socket import accept_and_authenticate
from app.modules.calls.api.schemas import (
    CallConfigResponse,
    CallResponse,
    OpenCallRequest,
)
from app.modules.calls.config import calls_settings
from app.modules.calls.services.engines import engines_offered

router = APIRouter(prefix="/pods/{pod_id}/calls", tags=["Calls"])
#: The socket authenticates itself; ``app/core/auth_exemptions.py`` lets its
#: handshake past the HTTP gate.
socket_router = APIRouter(tags=["live"])


@router.get(
    "/config",
    response_model=CallConfigResponse,
    operation_id="call.config",
    summary="Call configuration",
    description="Which voice engines this server can open a call with.",
    dependencies=[require_pod_membership("call agents in this pod")],
)
async def call_config(pod_id: UUID) -> CallConfigResponse:
    del pod_id
    return CallConfigResponse(engines=engines_offered(calls_settings))


@router.post(
    "",
    response_model=CallResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="call.open",
    summary="Open a call",
    dependencies=[require_pod_membership("call agents in this pod")],
    description=(
        "Open a live voice call with a pod agent, in the chat named by "
        "`conversation_id` or in a new conversation. The call is held in that "
        "conversation: its agent coordinates the call, hands work to sub-agents "
        "and shows things on the call's canvas. Connect to "
        "`/pods/{pod_id}/calls/{call_id}/socket` to talk."
    ),
    responses={
        404: {"description": "The agent, or the conversation, was not found."},
        409: {"description": "That conversation cannot hold a call."},
        422: {"description": "The caller cannot run the model the agent runs on."},
        403: {"description": "The caller may not run this agent."},
        503: {"description": "No voice engine is configured."},
    },
)
async def open_call_route(
    pod_id: UUID,
    body: OpenCallRequest,
    user: CurrentUser,
    request: Request,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> CallResponse:
    engines = engines_offered(calls_settings)
    if not engines:
        raise DomainError(
            "Voice calls are not set up on this server.",
            code="CALLS_NOT_CONFIGURED",
            status_code=503,
        )
    # Here rather than at import: these reach the agent module's runtime, and
    # every process that merely registers the route would pay for them.
    from app.modules.agent.contracts.calls import open_call
    from app.modules.calls.services.coordinator_runtime import coordinator_runtime

    # A pin for a new conversation only: an existing chat keeps its model.
    runtime = (
        await coordinator_runtime(
            calls_settings.call_coordinator_model, user_id=user.id, pod_id=pod_id
        )
        if body.conversation_id is None
        else None
    )
    async with pod_context_scope(
        uow_factory, request=request, user_id=user.id, pod_id=pod_id
    ) as scope:
        opened = await open_call(
            scope.uow,
            pod_id=pod_id,
            user_id=user.id,
            title=body.title or f"Call with {body.display_name}",
            agent_name=body.agent_name,
            conversation_id=body.conversation_id,
            runtime=runtime,
        )
    return CallResponse(call_id=opened.conversation_id, engines=engines)


@socket_router.websocket("/pods/{pod_id}/calls/{call_id}/socket")
async def call_socket(websocket: WebSocket, pod_id: UUID, call_id: UUID) -> None:
    session = await accept_and_authenticate(websocket)
    if session is None:
        return
    # Lazily, for the same reason as `open_call_route`: holding a call loads
    # the voice engines and the agent contracts.
    from app.modules.calls.services.hold import hold_call

    await hold_call(websocket, pod_id=pod_id, call_id=call_id, user_id=session.user_id)
