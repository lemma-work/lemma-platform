"""``/pods/{pod_id}/datastore/changes``: the previous change socket, kept as a shim.

Superseded by ``/pods/{pod_id}/live`` (see ``live_controller``). It stays for
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
from app.core.log.log import get_logger
from app.modules.datastore.api.live_socket import (
    CLOSE_UNAUTHENTICATED,
    accept_and_authenticate,
    close_code_for,
    serve,
)
from app.modules.datastore.domain.live import LiveCursorExpired, SubscribeRequest
from app.modules.datastore.services.live import live_hubs
from app.modules.datastore.services.live.connection import LiveConnection
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
    user_id = await accept_and_authenticate(websocket)
    if user_id is None:
        return
    try:
        await _authorize(pod_id, user_id, table)
    # The base class: a missing pod permission is raised by core authorization
    # as a plain 403 DomainError, and a client must stop on 4403 rather than
    # retry a 1011 forever.
    except DomainError as exc:
        await websocket.close(code=close_code_for(exc.status_code), reason=exc.message)
        return

    registry = live_hubs()
    connection = LiveConnection(
        pod_id=pod_id,
        user_id=user_id,
        send_text=websocket.send_text,
        queue_bytes=registry.settings.live_socket_queue_bytes,
        legacy=True,
    )
    hub = await registry.hub(pod_id)
    hub.attach(connection)
    try:
        await hub.subscribe(
            connection,
            SubscribeRequest(
                op="subscribe", id=_LEGACY_SUBSCRIPTION, table=table, since=since
            ),
            max_subscriptions=1,
        )
    except SQLAlchemyError, OSError, LiveCursorExpired:
        hub.detach(connection)
        logger.error(
            "datastore.live.socket_rejected", pod_id=str(pod_id), exc_info=True
        )
        await websocket.close(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Internal error while starting the datastore changes websocket.",
        )
        return
    await serve(websocket, connection, hub, max_subscriptions=1, keep_slot=False)
