"""What the live socket and its ``/datastore/changes`` shim share.

Authentication, the close codes, and the loop that runs one socket: a sender
draining the connection's bounded queue, a receiver dispatching client frames
to the pod hub, and a timer keeping the socket's slot claimed.

Rejections are closes *after* accept. A close before ``accept()`` never reaches
a browser as a close frame -- it sees the failed upgrade as 1006 and cannot
tell an expired session from a network drop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect, status
from sqlalchemy.exc import SQLAlchemyError
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError
from supertokens_python.recipe.session.interfaces import SessionContainer

from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.datastore.domain.live import (
    PONG_FRAME,
    LiveCursorExpired,
    LiveErrorCode,
    LiveRequestInvalid,
    PingRequest,
    SubscribeRequest,
    UnsubscribeRequest,
    error_frame,
    parse_request,
    unsubscribed_frame,
)
from app.modules.datastore.services.live.connection import LiveConnection
from app.modules.datastore.services.live.hub import PodHub
from app.modules.datastore.services.live.limits import (
    SLOT_REFRESH_SECONDS,
    refresh_socket_slot,
)

logger = get_logger(__name__)

#: The session is missing, invalid or expired: refresh the token and reconnect.
#: Same code as the browser-view socket and every SDK's live client.
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
#: The user already holds as many sockets on this pod as they may.
CLOSE_TOO_MANY_SOCKETS = 4429

#: Largest client frame the server reads. Subscribe frames are small.
MAX_CLIENT_FRAME_BYTES = 16 * 1024


async def _resolve_session(websocket: WebSocket) -> SessionContainer:
    """A SuperTokens session from the handshake.

    Tries, in order: ``Authorization: Bearer`` (CLI/SDK), the access-token
    cookie (browser cookie sessions), then an ``access_token`` query parameter
    (browsers cannot set headers on an upgrade).
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
        raise PermissionError("The live socket requires a session token.")
    session = await get_session_without_request_response(
        token, anti_csrf_check=False, session_required=True
    )
    if session is None:
        raise PermissionError("The live socket requires a session token.")
    return session


async def accept_and_authenticate(websocket: WebSocket) -> UUID | None:
    """Accept the handshake, then resolve the caller; None means it was closed."""
    try:
        await websocket.accept()
    except RuntimeError:
        # The client went away before the accept.
        return None
    try:
        session = await _resolve_session(websocket)
    except TryRefreshTokenError:
        await websocket.close(
            code=CLOSE_UNAUTHENTICATED,
            reason="Access token expired. Refresh your session and reconnect.",
        )
        return None
    except Exception:
        logger.debug(
            "datastore.live.session_resolution_failed.diagnostic", exc_info=True
        )
        await websocket.close(
            code=CLOSE_UNAUTHENTICATED, reason="Unauthorized live socket."
        )
        return None
    return UUID(session.get_user_id())


def close_code_for(status_code: int) -> int:
    """Map a domain HTTP status to a websocket close code."""
    if status_code == 404:
        return CLOSE_NOT_FOUND
    if status_code == 403:
        return CLOSE_FORBIDDEN
    return status.WS_1008_POLICY_VIOLATION


async def _receive(
    websocket: WebSocket,
    connection: LiveConnection,
    hub: PodHub,
    max_subscriptions: int,
) -> None:
    """Dispatch client frames until the client goes away."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        raw = message.get("text")
        if raw is None:
            payload = message.get("bytes")
            raw = payload.decode("utf-8", errors="replace") if payload else None
        if connection.legacy or raw is None:
            # The shim's clients send nothing meaningful; reading only notices
            # the disconnect.
            continue
        if len(raw) > MAX_CLIENT_FRAME_BYTES:
            connection.offer(
                error_frame(None, LiveErrorCode.INVALID, "Frame too large.")
            )
            continue
        try:
            request = parse_request(raw)
        except LiveRequestInvalid as invalid:
            connection.offer(
                error_frame(
                    invalid.subscription_id, LiveErrorCode.INVALID, str(invalid)
                )
            )
            continue
        if isinstance(request, PingRequest):
            connection.offer(PONG_FRAME)
        elif isinstance(request, UnsubscribeRequest):
            if hub.unsubscribe(connection, request.id):
                connection.offer(unsubscribed_frame(request.id, "client"))
        elif isinstance(request, SubscribeRequest):
            try:
                await hub.subscribe(
                    connection, request, max_subscriptions=max_subscriptions
                )
            except (SQLAlchemyError, OSError, LiveCursorExpired) as exc:
                # One subscription failing to start (the database briefly
                # unreachable) must not take the socket and its others down.
                logger.error(
                    "datastore.live.subscribe_failed",
                    pod_id=str(connection.pod_id),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                connection.subscriptions.pop(request.id, None)
                connection.offer(
                    error_frame(
                        request.id, LiveErrorCode.UNAVAILABLE, "Could not start; retry."
                    )
                )


async def _keep_slot(refresh: Callable[[], Awaitable[None]]) -> None:
    while True:
        await asyncio.sleep(SLOT_REFRESH_SECONDS)
        await refresh()


async def serve(
    websocket: WebSocket,
    connection: LiveConnection,
    hub: PodHub,
    *,
    max_subscriptions: int,
    keep_slot: bool,
) -> None:
    """Run one socket until either side ends it."""
    tasks = {
        create_inherited_task(connection.run_sender(), name="live-socket-sender"),
        create_inherited_task(
            _receive(websocket, connection, hub, max_subscriptions),
            name="live-socket-receiver",
        ),
    }
    if keep_slot:
        tasks.add(
            create_inherited_task(
                _keep_slot(
                    lambda: refresh_socket_slot(
                        connection.pod_id, connection.user_id, connection.id
                    )
                ),
                name="live-socket-slot",
            )
        )
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            error = task.exception() if not task.cancelled() else None
            if error is not None and not isinstance(
                error, (WebSocketDisconnect, RuntimeError, OSError)
            ):
                logger.error(
                    "datastore.live.socket_failed",
                    pod_id=str(connection.pod_id),
                    error_type=type(error).__name__,
                    exc_info=error,
                )
    finally:
        connection.close()
        hub.detach(connection)
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            await websocket.close()
