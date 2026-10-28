"""Running one live socket: authentication, the loop, and its client frames.

The loop is three tasks: a sender draining the connection's bounded queue, a
receiver dispatching client frames, and a timer keeping the socket's slot (and
its user's presence) claimed. Topics and calls are looked up in the registry
(``topics.py``); this file knows none of them.

Rejections are closes *after* accept. A close before ``accept()`` never reaches
a browser as a close frame -- it sees the failed upgrade as 1006 and cannot
tell an expired session from a network drop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from opentelemetry import metrics
from sqlalchemy.exc import SQLAlchemyError
from supertokens_python.exceptions import SuperTokensError
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError
from supertokens_python.recipe.session.interfaces import SessionContainer

from app.core.live.connection import LiveConnection
from app.core.live.handshake_origin import cookie_socket_refusal
from app.core.live.limits import SLOT_REFRESH_SECONDS
from app.core.live.pod_access import close_code_for
from app.core.live.sessions import LiveSession, open_sockets
from app.core.live.protocol import (
    CLOSE_FORBIDDEN,
    CLOSE_UNAUTHENTICATED,
    MAX_CLIENT_FRAME_BYTES,
    PONG_FRAME,
    CallRequest,
    LiveAccessDenied,
    LiveErrorCode,
    LiveRequestInvalid,
    LiveUnavailable,
    PingRequest,
    SubscribeRequest,
    UnsubscribedReason,
    UnsubscribeRequest,
    parse_request,
)
from app.core.live.topics import LiveRegistry, live_registry
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
subscribes = meter.create_counter("lemma.live.subscribes")
calls_made = meter.create_counter("lemma.live.calls")

#: What a topic or call raises when it cannot decide just now.
_TRANSIENT = (SQLAlchemyError, OSError, LiveUnavailable)


def _handshake_token(websocket: WebSocket) -> tuple[str | None, bool]:
    """The handshake's session token, and whether it came from a cookie.

    Tries, in order: ``Authorization: Bearer`` (CLI/SDK), the access-token
    cookie (browser cookie sessions), then an ``access_token`` query parameter
    (browsers cannot set headers on an upgrade).
    """
    authorization = websocket.headers.get("authorization") or ""
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() == "bearer" and raw.strip():
        return raw.strip(), False
    cookie = websocket.cookies.get("sAccessToken") or websocket.cookies.get(
        "st-access-token"
    )
    if cookie:
        return cookie, True
    return websocket.query_params.get("access_token"), False


async def _resolve_session(websocket: WebSocket) -> tuple[SessionContainer, bool]:
    """A SuperTokens session from the handshake, and whether it was the
    cookie's -- the one credential a page gets by being opened."""
    token, from_cookie = _handshake_token(websocket)
    if not token:
        raise PermissionError("The live socket requires a session token.")
    session = await get_session_without_request_response(
        token, anti_csrf_check=False, session_required=True
    )
    if session is None:
        raise PermissionError("The live socket requires a session token.")
    return session, from_cookie


def _session_of(session: SessionContainer) -> LiveSession:
    expires = session.get_access_token_payload().get("exp")
    return LiveSession(
        user_id=UUID(session.get_user_id()),
        handle=session.get_handle(),
        access_expires_at=float(expires) if isinstance(expires, int | float) else None,
    )


async def accept_and_authenticate(websocket: WebSocket) -> LiveSession | None:
    """Accept the handshake, then resolve the caller's session; None means
    the socket was closed."""
    try:
        await websocket.accept()
    except RuntimeError:
        # The client went away before the accept.
        return None
    try:
        session, from_cookie = await _resolve_session(websocket)
    except TryRefreshTokenError:
        await websocket.close(
            code=CLOSE_UNAUTHENTICATED,
            reason="Access token expired. Refresh your session and reconnect.",
        )
        return None
    # Whatever SuperTokens says of a token it will not take, a missing token,
    # or one too malformed to read: all the same answer.
    except SuperTokensError, PermissionError, ValueError:
        logger.debug("live.session_resolution_failed.diagnostic", exc_info=True)
        await websocket.close(
            code=CLOSE_UNAUTHENTICATED, reason="Unauthorized live socket."
        )
        return None
    refusal = await _workload_refusal(websocket, session)
    if refusal is None and from_cookie:
        # No anti-CSRF check above: where the page that opened it is decides
        # (``handshake_origin``), and an app's page stays in its own pod.
        refusal = await cookie_socket_refusal(websocket)
    if refusal is not None:
        await websocket.close(code=CLOSE_FORBIDDEN, reason=refusal)
        return None
    return _session_of(session)


async def _workload_refusal(
    websocket: WebSocket, session: SessionContainer
) -> str | None:
    """Why a workload's token may not hold this socket, or None.

    The socket authenticates itself, so the global gate never ran, and its
    topics read as the user the token names. A workload's token got that
    person's whole reach here -- in any pod. It is resolved now as on every
    HTTP route: standing, revocation, and the pin to its own pod. The pod's
    assistant reads exactly what its person reads, so it keeps the socket in
    its own pod; a named agent or function would read past its grants, which
    the topics do not apply, so it is refused.
    """
    from app.core.authorization.delegation import is_default_pod_agent_claims
    from app.core.security import PrincipalRefused, resolve_delegated_principal

    raw_pod_id = websocket.path_params.get("pod_id")
    try:
        claims = await resolve_delegated_principal(
            session.get_access_token_payload() or {},
            user_id=UUID(session.get_user_id()),
            pod_id=UUID(str(raw_pod_id)) if raw_pod_id else None,
        )
    except PrincipalRefused as refused:
        return refused.message
    if claims is None or is_default_pod_agent_claims(claims):
        return None
    return "A named agent or function cannot hold a live socket."


class LiveDispatcher:
    """Acts on one socket's client frames, against the registered topics and
    calls."""

    def __init__(
        self, *, max_subscriptions: int, registry: LiveRegistry | None = None
    ) -> None:
        self._max_subscriptions = max_subscriptions
        self._registry = registry or live_registry()

    async def handle(self, connection: LiveConnection, raw: str) -> None:
        # Bytes, as the limit says: a character may take four. The length in
        # characters is a floor of it, checked first so a huge frame is not
        # encoded only to be refused.
        if (
            len(raw) > MAX_CLIENT_FRAME_BYTES
            or len(raw.encode("utf-8", errors="surrogatepass")) > MAX_CLIENT_FRAME_BYTES
        ):
            connection.offer_error(None, LiveErrorCode.INVALID, "Frame too large.")
            return
        try:
            request = parse_request(raw)
        except LiveRequestInvalid as invalid:
            connection.offer_error(
                invalid.request_id, LiveErrorCode.INVALID, str(invalid)
            )
            return
        match request:
            case PingRequest():
                connection.offer(PONG_FRAME)
            case UnsubscribeRequest():
                self._unsubscribe(connection, request.id)
            case SubscribeRequest():
                await self._subscribe(connection, request)
            case CallRequest():
                await self._call(connection, request)

    def _unsubscribe(self, connection: LiveConnection, subscription_id: str) -> None:
        if subscription_id in connection.subscriptions:
            connection.offer_unsubscribed(
                subscription_id, UnsubscribedReason.CLIENT.value
            )

    async def _subscribe(
        self, connection: LiveConnection, request: SubscribeRequest
    ) -> None:
        if request.id in connection.subscriptions:
            connection.offer_error(
                request.id, LiveErrorCode.INVALID, "Subscription id already in use."
            )
            return
        if len(connection.subscriptions) >= self._max_subscriptions:
            connection.offer_error(
                request.id,
                LiveErrorCode.LIMIT,
                f"At most {self._max_subscriptions} subscriptions per socket.",
            )
            return
        topic = self._registry.topics.get(request.topic)
        if topic is None:
            connection.offer_error(
                request.id,
                LiveErrorCode.INVALID,
                f"topic: no topic named {request.topic!r}.",
            )
            return
        subscribes.add(1, {"topic": request.topic})
        try:
            await topic.subscribe(connection, request)
        except LiveRequestInvalid as invalid:
            self._drop(connection, request.id)
            connection.offer_error(request.id, LiveErrorCode.INVALID, str(invalid))
        except LiveAccessDenied as denied:
            self._drop(connection, request.id)
            connection.offer_error(request.id, denied.code, denied.message)
        except _TRANSIENT as exc:
            # One subscription failing to start (the database briefly
            # unreachable) must not take the socket and its others down.
            logger.error(
                "live.subscribe.failed",
                pod_id=str(connection.pod_id),
                topic=request.topic,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            self._drop(connection, request.id)
            connection.offer_error(
                request.id, LiveErrorCode.UNAVAILABLE, "Could not start; retry."
            )

    @staticmethod
    def _drop(connection: LiveConnection, subscription_id: str) -> None:
        subscription = connection.subscriptions.pop(subscription_id, None)
        if subscription is not None:
            subscription.release()

    async def _call(self, connection: LiveConnection, request: CallRequest) -> None:
        call = self._registry.calls.get(request.method)
        if call is None:
            connection.offer_error(
                request.id,
                LiveErrorCode.INVALID,
                f"method: no method named {request.method!r}.",
            )
            return
        if not connection.admit_call(call.name, call.per_minute()):
            connection.offer_error(
                request.id,
                LiveErrorCode.LIMIT,
                f"At most {call.per_minute()} {call.name} calls a minute.",
            )
            return
        calls_made.add(1, {"method": call.name})
        try:
            result = await call(connection, request.params, call_id=request.id)
        except LiveRequestInvalid as invalid:
            connection.offer_error(request.id, LiveErrorCode.INVALID, str(invalid))
        except LiveAccessDenied as denied:
            connection.offer_error(request.id, denied.code, denied.message)
        except _TRANSIENT as exc:
            logger.error(
                "live.call.failed",
                pod_id=str(connection.pod_id),
                method=call.name,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            connection.offer_error(
                request.id, LiveErrorCode.UNAVAILABLE, "Could not do that; retry."
            )
        else:
            connection.offer_result(request.id, result)


#: Acts on one client frame; ``None`` for a socket whose client sends nothing
#: meaningful (it is read only to notice the disconnect).
FrameHandler = Callable[[LiveConnection, str], Awaitable[None]]


async def _receive(
    websocket: WebSocket, connection: LiveConnection, handle: FrameHandler | None
) -> None:
    """Dispatch client frames until the client goes away."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        raw = message.get("text")
        if raw is None:
            payload = message.get("bytes")
            if payload and len(payload) > MAX_CLIENT_FRAME_BYTES:
                # Only enough of it to be refused is decoded; ``handle`` refuses it.
                payload = payload[: MAX_CLIENT_FRAME_BYTES + 1]
            raw = payload.decode("utf-8", errors="replace") if payload else None
        if handle is None or raw is None:
            continue
        await handle(connection, raw)


async def _keep(refresh: Callable[[], Awaitable[None]]) -> None:
    while True:
        await asyncio.sleep(SLOT_REFRESH_SECONDS)
        await refresh()


async def serve(
    websocket: WebSocket,
    connection: LiveConnection,
    *,
    handle: FrameHandler | None,
    session: LiveSession,
    keep: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run one socket until either side ends it. ``keep`` runs every
    ``SLOT_REFRESH_SECONDS`` while it is open: what it holds stays claimed.
    The socket closes when its ``session`` is announced ended
    (``sessions.OpenSockets``), or when the server ends it with a code
    (``LiveConnection.close_with``), with that code."""
    sockets = open_sockets()
    sockets.hold(connection, session)
    tasks = {
        create_inherited_task(connection.run_sender(), name="live-socket-sender"),
        create_inherited_task(
            _receive(websocket, connection, handle), name="live-socket-receiver"
        ),
    }
    if keep is not None:
        tasks.add(create_inherited_task(_keep(keep), name="live-socket-keep"))
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            error = task.exception() if not task.cancelled() else None
            if error is not None and not isinstance(
                error, (WebSocketDisconnect, RuntimeError, OSError)
            ):
                logger.error(
                    "live.socket.failed",
                    pod_id=str(connection.pod_id),
                    error_type=type(error).__name__,
                    exc_info=error,
                )
    finally:
        sockets.release(connection, session)
        connection.close()
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            if connection.close_code is None:
                await websocket.close()
            else:
                await websocket.close(
                    code=connection.close_code, reason=connection.close_reason
                )


__all__ = [
    "FrameHandler",
    "LiveDispatcher",
    "accept_and_authenticate",
    "close_code_for",
    "serve",
]
