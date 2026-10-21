"""``/pods/{pod_id}/live``: one socket per (client, pod), many subscriptions.

The protocol is in ``protocol.py`` and ``docs/modules/live.md``. This handler
authenticates, checks the caller may see the pod at all, holds one of the
caller's socket slots and their presence, and runs the socket. Each topic
checks its own permission per subscription, and each call per call.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, WebSocket

from app.core.domain.errors import DomainError
from app.core.live.config import live_settings
from app.core.live.connection import LiveConnection
from app.core.live.limits import (
    claim_socket_slot,
    current_live_limit_port,
    refresh_socket_slot,
    release_socket_slot,
)
from app.core.live.pod_access import (
    close_code_for,
    keep_pod_access,
    require_pod_access,
)
from app.core.live.presence import mark_offline, mark_online
from app.core.live.protocol import CLOSE_TOO_MANY_SOCKETS, ready_frame
from app.core.live.sessions import SessionWatch, end_for_session
from app.core.live.socket import LiveDispatcher, accept_and_authenticate, serve
from app.core.live.topics import live_registry

router = APIRouter(tags=["live"])

#: ``verify_auth`` lets this path's handshake through; the handler
#: authenticates it. Matched in ``app/core/auth_exemptions.py``.
POD_LIVE_WS_SUFFIX = "/live"


@router.websocket("/pods/{pod_id}/live")
async def pod_live_ws(websocket: WebSocket, pod_id: UUID) -> None:
    session = await accept_and_authenticate(websocket)
    if session is None:
        return
    user_id = session.user_id
    try:
        await require_pod_access(pod_id, user_id)
    except DomainError as exc:
        await websocket.close(code=close_code_for(exc.status_code), reason=exc.message)
        return

    limits = await current_live_limit_port().limits_for(pod_id)
    connection = LiveConnection(
        pod_id=pod_id,
        user_id=user_id,
        send_text=websocket.send_text,
        queue_bytes=live_settings.live_socket_queue_bytes,
    )
    if not await claim_socket_slot(
        pod_id, user_id, connection.id, limits.sockets_per_user
    ):
        await websocket.close(
            code=CLOSE_TOO_MANY_SOCKETS,
            reason=f"At most {limits.sockets_per_user} live sockets per user per pod.",
        )
        return

    watch = SessionWatch(session)

    async def keep() -> None:
        if not await watch.still_valid():
            end_for_session(connection)
            return
        if not await keep_pod_access(connection):
            return
        await refresh_socket_slot(pod_id, user_id, connection.id)
        await mark_online(pod_id, user_id)

    registry = live_registry()
    try:
        await mark_online(pod_id, user_id)
        connection.offer(
            ready_frame(
                connection.id,
                topics=sorted(registry.topics),
                calls=sorted(registry.calls),
            )
        )
        dispatcher = LiveDispatcher(
            max_subscriptions=limits.subscriptions_per_socket, registry=registry
        )
        await serve(
            websocket,
            connection,
            handle=dispatcher.handle,
            session=session,
            keep=keep,
        )
    finally:
        await release_socket_slot(pod_id, user_id, connection.id)
        await mark_offline(pod_id, user_id)


__all__ = ["POD_LIVE_WS_SUFFIX", "router"]
