"""WebSocket endpoint streaming live datastore record changes to a user.

Clients connect to ``/pods/{pod_id}/datastore/changes`` and receive a JSON frame
for every record insert/update/delete in the pod they are allowed to see. The
endpoint is read-only and one-directional (server → client); any inbound frames
are ignored and only serve to detect disconnects.

Design notes
------------
* **Source.** Record writes already publish ``DatastoreRecordEvent`` to the
  unified ``datastore.events`` Redis stream. This handler tails that stream
  directly (per connection) via :class:`PubSubSubscriber`, so nothing new is
  published and there is no dependency on the worker process.
* **Auth.** ``verify_auth`` skips this path for websockets (it cannot run the
  session machinery without an HTTP response), so the session is resolved here
  manually — from a bearer ``Authorization`` header (CLI/SDK), the SuperTokens
  access-token cookie (browser), or an ``access_token`` query param fallback.
* **Visibility.** At connect we snapshot the set of tables the user can read
  (``list_tables`` already filters to visible tables). RLS row scoping needs no
  database read on the hot path: each event carries ``owner_user_id`` (the row
  owner for RLS tables, ``None`` for shared/non-RLS tables), so a set owner is
  delivered only to that user while a ``None`` owner fans out to every reader.
  Tables created *after* connect are not streamed until the client reconnects.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Iterable
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.pubsub.subscriber import PubSubSubscriber
from app.modules.datastore.api.dependencies import build_table_service
from app.modules.datastore.domain.errors import DatastoreDomainError
from app.modules.datastore.domain.events import DATASTORE_EVENTS_STREAM

logger = get_logger(__name__)

router = APIRouter(tags=["datastore-changes"])

_RECORD_EVENT_PREFIX = "datastore.record."

# Path used by ``verify_auth`` to allowlist this websocket. Kept here so the two
# stay in sync; the security layer matches ``/pods/{uuid}/datastore/changes``.
DATASTORE_CHANGES_WS_SUFFIX = "/datastore/changes"


async def _resolve_session(websocket: WebSocket):
    """Resolve a SuperTokens session from a websocket handshake.

    Tries, in order: ``Authorization: Bearer`` (CLI/SDK), the access-token
    cookie (browser cookie sessions), then an ``access_token`` query parameter
    (for browser clients that cannot attach the cookie cross-site).
    """
    token: str | None = None
    authorization = websocket.headers.get("authorization") or ""
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() == "bearer" and raw.strip():
        token = raw.strip()
    if token is None:
        token = (
            websocket.cookies.get("sAccessToken")
            or websocket.cookies.get("st-access-token")
            or websocket.query_params.get("access_token")
        )
    if not token:
        raise PermissionError("Datastore changes websocket requires a session token.")
    return await get_session_without_request_response(
        token,
        anti_csrf_check=False,
        session_required=True,
    )


async def _visible_table_names(table_service, pod_id: UUID, ctx: Context) -> set[str]:
    """Snapshot every table name the caller can currently read in the pod."""
    names: set[str] = set()
    cursor: str | None = None
    while True:
        tables, cursor = await table_service.list_tables(
            pod_id, ctx, limit=200, cursor=cursor
        )
        names.update(table.table_name for table in tables)
        if not cursor:
            break
    return names


def _record_frame(
    event: dict,
    *,
    pod_id: UUID,
    user_id: UUID,
    allowed_tables: Iterable[str],
) -> dict | None:
    """Build a client frame from a stream event, or ``None`` to drop it.

    Drops anything that is not a record event, is for another pod, is for a
    table the caller cannot see, or — for RLS tables — belongs to another user.
    """
    event_type = event.get("event_type", "")
    if not event_type.startswith(_RECORD_EVENT_PREFIX):
        return None
    if str(event.get("pod_id")) != str(pod_id):
        return None
    table_name = event.get("table_name")
    if table_name not in allowed_tables:
        return None
    # RLS row scoping: a set owner means a per-user (RLS) row — deliver only to
    # its owner. ``None`` means a shared/non-RLS row — deliver to every reader.
    owner_user_id = event.get("owner_user_id")
    if owner_user_id is not None and str(owner_user_id) != str(user_id):
        return None
    return {
        "type": event_type,
        "pod_id": str(pod_id),
        "table_name": table_name,
        "record_id": event.get("record_id"),
        "operation": event.get("operation"),
        "payload": event.get("payload") or {},
        "occurred_at": event.get("occurred_at"),
        "stream_id": event.get("_stream_id"),
    }


async def _forward_changes(
    websocket: WebSocket,
    *,
    pod_id: UUID,
    user_id: UUID,
    allowed_tables: set[str],
    since: str | None,
) -> None:
    """Tail the datastore stream and forward matching frames to the client.

    Anchors the read at the stream's current last id (or ``since`` for a resume)
    and announces it in a ``ready`` frame, so the client knows the stream is live
    and holds a resume cursor before any change frames arrive.
    """
    subscriber = PubSubSubscriber(DATASTORE_EVENTS_STREAM)
    async with subscriber:
        start_id = since or await subscriber.current_last_id()
        await websocket.send_json({"type": "ready", "since": start_id})
        async for event in subscriber.subscribe(start_id=start_id):
            frame = _record_frame(
                event,
                pod_id=pod_id,
                user_id=user_id,
                allowed_tables=allowed_tables,
            )
            if frame is not None:
                await websocket.send_json(frame)


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    """Drain inbound frames so a client disconnect is noticed promptly."""
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            await websocket.receive()


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
            "Resume after a previously seen Redis stream id (the `stream_id` "
            "field of an earlier frame) to replay missed changes. Omit to start "
            "from new changes only."
        ),
    ),
) -> None:
    try:
        session = await _resolve_session(websocket)
    except TryRefreshTokenError:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Access token expired. Refresh your session and reconnect.",
        )
        return
    except Exception:
        logger.warning(
            "Session resolution failed for datastore changes websocket",
            exc_info=True,
        )
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Unauthorized datastore changes websocket.",
        )
        return

    user_id = UUID(session.get_user_id())
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)

    # Authorize and snapshot visibility within a short-lived session, then close
    # it: the streaming loop below needs no database access.
    try:
        async with uow_factory() as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id,
                pod_id=pod_id,
            )
            table_service = build_table_service(uow)
            if table is not None:
                # get_table enforces table read and existence in one step.
                entity = await table_service.get_table(pod_id, table, ctx)
                allowed_tables = {entity.table_name}
            else:
                await ctx.require(
                    Permissions.DATASTORE_TABLE_READ, ResourceRef.pod(pod_id)
                )
                allowed_tables = await _visible_table_names(
                    table_service, pod_id, ctx
                )
    except DatastoreDomainError as exc:
        await websocket.close(
            code=_close_code_for(exc.status_code),
            reason=exc.message,
        )
        return
    except Exception:
        logger.warning(
            "Rejected datastore changes websocket",
            pod_id=str(pod_id),
            user_id=str(user_id),
            exc_info=True,
        )
        await websocket.close(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Internal error while authorizing datastore changes websocket.",
        )
        return

    try:
        await websocket.accept()
    except RuntimeError:
        # Client disconnected before we could accept (race on connect).
        return

    forwarder = asyncio.create_task(
        _forward_changes(
            websocket,
            pod_id=pod_id,
            user_id=user_id,
            allowed_tables=allowed_tables,
            since=since,
        )
    )
    disconnect = asyncio.create_task(_wait_for_disconnect(websocket))
    try:
        await asyncio.wait(
            {forwarder, disconnect}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (forwarder, disconnect):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            await websocket.close()


def _close_code_for(status_code: int) -> int:
    """Map a datastore HTTP status to a websocket close code."""
    if status_code == 404:
        return 4404
    if status_code == 403:
        return 4403
    return status.WS_1008_POLICY_VIOLATION
