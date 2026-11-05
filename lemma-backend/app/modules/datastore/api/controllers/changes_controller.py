"""``/pods/{pod_id}/datastore/changes``: the previous change socket, kept as a shim.

Superseded by ``/pods/{pod_id}/live`` (``app/core/live``). It stays for
one release so app bundles built against the previous SDK keep working: the
socket is one implicit subscription on the same per-process pod hub, framed the
way those bundles read it -- a ``ready`` with ``since``, then one frame per
change carrying ``stream_id``.

Differences a previous client can notice, all of them safe for it:

* ``stream_id`` is now a change-log cursor. A Redis stream id it saved before
  the upgrade reads as expired; the socket answers with a fresh ``ready`` and
  continues from now.
* A burst too large to send row by row, or a socket that falls behind, gets a
  fresh ``ready`` at the new position instead of every row.
* A delete carries an empty ``payload``.
* Losing access to what it watches closes the socket with ``4403``, and the
  session ending closes it with ``4401``, rather than leaving it open and
  silent.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.config import live_settings
from app.core.live.connection import LiveConnection
from app.core.live.protocol import (
    CLOSE_FORBIDDEN,
    CLOSE_UNAUTHENTICATED,
    LiveAccessDenied,
    LiveUnavailable,
)
from app.core.live.sessions import SessionWatch, end_for_session
from app.core.live.socket import accept_and_authenticate, close_code_for, serve
from app.core.log.log import get_logger
from app.modules.datastore.domain.live import LiveCursorExpired, SubscribeRequest
from app.modules.datastore.services.live import live_hubs
from app.modules.datastore.services.live.subscription import LegacyFrames
from app.modules.datastore.services.wiring import build_table_service

logger = get_logger(__name__)

router = APIRouter(tags=["datastore-changes"])

# Path used by ``verify_auth`` to allowlist this websocket. Kept here so the two
# stay in sync; the security layer matches ``/pods/{uuid}/datastore/changes``.
DATASTORE_CHANGES_WS_SUFFIX = "/datastore/changes"

__all__ = ["CLOSE_UNAUTHENTICATED", "DATASTORE_CHANGES_WS_SUFFIX", "router"]

_LEGACY_SUBSCRIPTION = "changes"


async def _authorize(pod_id: UUID, user_id: UUID, table: str | None) -> None:
    """Refuse up front, with the close codes the previous SDK stops on."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        if table is not None:
            entity = await build_table_service(uow).get_table(pod_id, table, ctx)
            # Seeing the table is not reading its rows; the frames carry rows.
            await ctx.require(
                Permissions.DATASTORE_RECORD_READ, ResourceRef.table(pod_id, entity.id)
            )
        else:
            await ctx.require(Permissions.DATASTORE_TABLE_READ, ResourceRef.pod(pod_id))


@router.websocket("/pods/{pod_id}/datastore/changes")
async def datastore_changes_ws(
    websocket: WebSocket,
    pod_id: UUID,
    table: str | None = Query(
        default=None,
        description=(
            "Restrict the stream to a single table by name. Omit to receive "
            "changes for every table in the pod the caller can read."
        ),
    ),
    since: str | None = Query(
        default=None,
        description=(
            "Resume after a previously seen cursor (the `stream_id` field of an "
            "earlier frame) to replay missed changes. Omit to start from new "
            "changes only."
        ),
    ),
) -> None:
    session = await accept_and_authenticate(websocket)
    if session is None:
        return
    user_id = session.user_id
    try:
        await _authorize(pod_id, user_id, table)
    # The base class: a missing pod permission is raised by core authorization
    # as a plain 403 DomainError, and a client must stop on 4403 rather than
    # retry a 1011 forever.
    except DomainError as exc:
        await websocket.close(code=close_code_for(exc.status_code), reason=exc.message)
        return

    connection = LiveConnection(
        pod_id=pod_id,
        user_id=user_id,
        send_text=websocket.send_text,
        queue_bytes=live_settings.live_socket_queue_bytes,
        frames=LegacyFrames(),
        # Its client has no frame for "unsubscribed": losing access to what it
        # watches closes the socket (4403), which that client stops on.
        ends_with_subscription=True,
    )
    hub = await live_hubs().hub(pod_id)
    try:
        await hub.subscribe(
            connection,
            SubscribeRequest(id=_LEGACY_SUBSCRIPTION, table=table, since=since),
            legacy=True,
        )
    except LiveAccessDenied as denied:
        # Access checked above and lost since: stop, as the old SDK does on 4403.
        connection.close()
        await websocket.close(code=CLOSE_FORBIDDEN, reason=denied.message)
        return
    except SQLAlchemyError, OSError, LiveCursorExpired, LiveUnavailable:
        connection.close()
        logger.error(
            "datastore.live.socket_rejected", pod_id=str(pod_id), exc_info=True
        )
        await websocket.close(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Internal error while starting the datastore changes websocket.",
        )
        return
    watch = SessionWatch(session)

    async def keep() -> None:
        if not await watch.still_valid():
            end_for_session(connection)

    await serve(websocket, connection, handle=None, session=session, keep=keep)
