"""``/pods/{pod_id}/live``: one socket per (client, pod), many subscriptions.

The protocol is in ``domain/live.py``; the per-process pod hub that feeds every
socket is in ``services/live/hub.py``. This handler authenticates, checks the
caller may see the pod at all, holds one of the caller's socket slots, and
hands the socket to the hub. Table permission is checked per subscription.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, WebSocket, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.datastore.api.live_socket import (
    CLOSE_TOO_MANY_SOCKETS,
    accept_and_authenticate,
    close_code_for,
    serve,
)
from app.modules.datastore.domain.live import LiveCursorExpired, ready_frame
from app.modules.datastore.services.live import live_hubs
from app.modules.datastore.services.live.connection import LiveConnection
from app.modules.datastore.services.live.limits import (
    claim_socket_slot,
    current_live_limit_port,
    release_socket_slot,
)

logger = get_logger(__name__)

router = APIRouter(tags=["live"])

#: ``verify_auth`` lets this path's handshake through; the handler
#: authenticates it. Matched in ``app/core/auth_exemptions.py``.
POD_LIVE_WS_SUFFIX = "/live"


async def _require_pod_access(pod_id: UUID, user_id: UUID) -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        await ctx.require(Permissions.POD_READ, ResourceRef.pod(pod_id))


@router.websocket("/pods/{pod_id}/live")
async def pod_live_ws(websocket: WebSocket, pod_id: UUID) -> None:
    user_id = await accept_and_authenticate(websocket)
    if user_id is None:
        return
    try:
        await _require_pod_access(pod_id, user_id)
    except DomainError as exc:
        await websocket.close(code=close_code_for(exc.status_code), reason=exc.message)
        return

    limits = await current_live_limit_port().limits_for(pod_id)
    registry = live_hubs()
    connection = LiveConnection(
        pod_id=pod_id,
        user_id=user_id,
        send_text=websocket.send_text,
        queue_bytes=registry.settings.live_socket_queue_bytes,
    )
    if not await claim_socket_slot(
        pod_id, user_id, connection.id, limits.sockets_per_user
    ):
        await websocket.close(
            code=CLOSE_TOO_MANY_SOCKETS,
            reason=f"At most {limits.sockets_per_user} live sockets per user per pod.",
        )
        return
    try:
        hub = await registry.hub(pod_id)
        hub.attach(connection)
        try:
            await hub.ensure_started()
        except SQLAlchemyError, OSError, LiveCursorExpired:
            hub.detach(connection)
            logger.error(
                "datastore.live.socket_rejected", pod_id=str(pod_id), exc_info=True
            )
            await websocket.close(
                code=status.WS_1011_INTERNAL_ERROR,
                reason="The pod's live feed is unavailable; retry shortly.",
            )
            return
        connection.offer(ready_frame(connection.id, hub.cursor))
        await serve(
            websocket,
            connection,
            hub,
            max_subscriptions=limits.subscriptions_per_socket,
            keep_slot=True,
        )
    finally:
        await release_socket_slot(pod_id, user_id, connection.id)
